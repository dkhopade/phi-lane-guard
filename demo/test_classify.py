import yaml
from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer

POLICY = yaml.safe_load(open('../gateway/policy.yaml'))
D = POLICY['detection']
THRESHOLD = float(D['score_threshold'])
ET = {k: float(v) for k, v in (D.get('entity_thresholds') or {}).items()}
ENTITIES, STRONG = list(D['entities']), set(D['strong'])

engine = AnalyzerEngine()
engine.registry.add_recognizer(PatternRecognizer(
    supported_entity="MRN",
    patterns=[Pattern("mrn_labelled", r"\bMRN[-#:\s]{0,3}\d{6,10}\b", 0.85),
              Pattern("mrn_bare", r"\b(?:MR|MRN)\d{6,10}\b", 0.8)],
    context=["medical","record","patient","chart"]))

def classify(text):
    res = engine.analyze(text=text, entities=ENTITIES, language="en")
    f = [(r.entity_type, round(r.score,2)) for r in res
         if r.score >= ET.get(r.entity_type, THRESHOLD)]
    return any(e in STRONG for e,_ in f), sorted({e for e,_ in f})

CASES = [
 ("PHI note", "Patient Marcus Delgado, MRN 4471822, DOB 1968-03-14, reachable at 919-555-0177. Presented with exertional dyspnea. Echo shows EF 38%.", True),
 ("clean clinical", "Summarize first-line pharmacologic management of HFrEF. Drug classes only, no patient specifics.", False),
 ("dosing question", "What is standard dosing for metoprolol succinate in HFrEF?", False),
 ("trial statistics", "The trial enrolled 1200 patients across 14 sites in 2019.", False),
 ("staff phone", "Contact the on-call cardiologist at 919-555-0143 about bed availability.", None),
 ("SSN present", "Member SSN 078-05-1120 needs eligibility verification.", True),
 ("email present", "Send the discharge summary to r.okafor@example-health.org", True),
 ("name only", "Dr. Patel reviewed the imaging protocol.", None),
]
ok = True
for name, t, expect in CASES:
    phi, ents = classify(t)
    mark = "   " if expect is None else ("PASS" if phi == expect else "FAIL")
    if expect is not None and phi != expect: ok = False
    print(f"{mark}  {'PHI' if phi else '   '}  {name:18s} {ents}")
print("\nAll asserted cases passed." if ok else "\nSOME CASES FAILED")
