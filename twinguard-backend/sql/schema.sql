-- TwinGuard schema for Supabase. Run once in the Supabase SQL editor.
-- The API uses the service-role key (bypasses RLS); RLS is enabled with no
-- policies so the public anon key can read nothing.

create table if not exists products (
  product_id text primary key, name text not null, unit_price double precision not null);
create table if not exists suppliers (
  supplier_id text primary key, name text not null, lead_time_days int);
create table if not exists warehouses (
  warehouse_id text primary key, name text not null, capacity int not null);
create table if not exists inventory_records (
  product_id text not null, warehouse_id text not null,
  quantity_on_hand int not null, quantity_reserved int not null,
  last_synced_at timestamptz not null, primary key (product_id, warehouse_id));
create table if not exists customer_orders (
  order_id text primary key, product_id text not null, quantity int not null,
  priority text not null, due_date date not null, status text not null);
create table if not exists purchase_orders (
  po_id text primary key, product_id text not null, supplier_id text not null,
  quantity int not null, expected_arrival date not null, status text not null,
  destination_warehouse_id text);
create table if not exists business_rules (
  id int primary key default 1 check (id = 1),
  max_transfer_quantity int not null, max_purchase_budget double precision not null,
  freshness_limit_hours int not null, allow_negative_inventory boolean not null default false);
create table if not exists store_meta (
  id int primary key default 1 check (id = 1), version int not null);

create table if not exists users (
  username text primary key, password_hash text not null,
  role text not null check (role in ('planner','manager','viewer')));
create table if not exists requests (
  id text primary key, text text not null, created_by text not null,
  status text not null, created_at timestamptz not null default now(),
  action jsonb, decision text, reason_code text, report jsonb, explanation text,
  clarification text, error text, snapshot_version int);
create table if not exists agent_steps (
  request_id text not null, idx int not null, tool text not null, summary text not null,
  primary key (request_id, idx));
create table if not exists review_tasks (
  request_id text primary key, status text not null default 'pending',
  action jsonb not null, reasons jsonb not null default '[]',
  created_at timestamptz not null default now(), decided_by text, comment text);
create table if not exists audit_events (
  id text primary key, request_id text, kind text not null, actor text not null,
  at timestamptz not null default now(), payload jsonb not null default '{}');
create table if not exists snapshots (
  snapshot_id text primary key, source_version int not null,
  created_at timestamptz not null, freshness_status text not null);

alter table products enable row level security;
alter table suppliers enable row level security;
alter table warehouses enable row level security;
alter table inventory_records enable row level security;
alter table customer_orders enable row level security;
alter table purchase_orders enable row level security;
alter table business_rules enable row level security;
alter table store_meta enable row level security;
alter table users enable row level security;
alter table requests enable row level security;
alter table agent_steps enable row level security;
alter table review_tasks enable row level security;
alter table audit_events enable row level security;
alter table snapshots enable row level security;

-- The single write path for business data. Atomic: any error (including a
-- version mismatch) rolls back the version bump and every row change.
create or replace function apply_approved_action(p_action jsonb, p_expected_version int)
returns int language plpgsql as $$
declare
  v int;
  t text := p_action->>'action_type';
  p jsonb := p_action->'parameters';
  lead int;
  n int;
begin
  update store_meta set version = version + 1
    where id = 1 and version = p_expected_version returning version into v;
  if v is null then raise exception 'stale_version'; end if;

  if t = 'transfer_inventory' then
    update inventory_records set quantity_on_hand = quantity_on_hand - (p->>'quantity')::int
      where product_id = p->>'product_id' and warehouse_id = p->>'source_warehouse_id';
    if not found then raise exception 'no inventory row for source'; end if;
    update inventory_records set quantity_on_hand = quantity_on_hand + (p->>'quantity')::int
      where product_id = p->>'product_id' and warehouse_id = p->>'destination_warehouse_id';
    if not found then raise exception 'no inventory row for destination'; end if;
  elsif t = 'create_purchase_order' then
    select lead_time_days into lead from suppliers where supplier_id = p->>'supplier_id';
    select count(*) into n from purchase_orders;
    insert into purchase_orders (po_id, product_id, supplier_id, quantity,
                                 expected_arrival, status, destination_warehouse_id)
      values ('PO-NEW-' || lpad((n + 1)::text, 4, '0'), p->>'product_id', p->>'supplier_id',
              (p->>'quantity')::int, current_date + coalesce(lead, 7), 'pending',
              p->>'destination_warehouse_id');
  elsif t = 'change_order_priority' then
    update customer_orders set priority = p->>'new_priority' where order_id = p->>'order_id';
    if not found then raise exception 'no customer order'; end if;
  elsif t = 'cancel_purchase_order' then
    update purchase_orders set status = 'cancelled' where po_id = p->>'po_id';
    if not found then raise exception 'no purchase order'; end if;
  else
    raise exception 'unsupported action_type %', t;
  end if;
  return v;
end $$;
