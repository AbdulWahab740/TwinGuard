from twinguard_core.eval import run_detailed_evaluation

report = run_detailed_evaluation(500, seed=100)
print(report["clean"])
print(report["singles"])
print(report["pairs"])