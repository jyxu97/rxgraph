# Curated seed list: ~100 high-interaction drugs across key drug classes.
# These are generic names (lowercase), matching how OpenFDA indexes them
# and how they'll be stored in Neo4j.

SEED_DRUGS = [
    # Anticoagulants — highest interaction density
    "warfarin",
    "heparin",
    "apixaban",
    "rivaroxaban",
    "dabigatran",
    "enoxaparin",

    # NSAIDs + Analgesics — common OTC, dangerous with anticoagulants
    "ibuprofen",
    "aspirin",
    "naproxen",
    "celecoxib",
    "diclofenac",
    "acetaminophen",

    # SSRIs / SNRIs — serotonin syndrome risk
    "fluoxetine",
    "sertraline",
    "paroxetine",
    "citalopram",
    "escitalopram",
    "venlafaxine",
    "duloxetine",

    # MAOIs — classic dangerous interactions
    "phenelzine",
    "tranylcypromine",
    "selegiline",

    # TCAs
    "amitriptyline",
    "nortriptyline",
    "imipramine",

    # Statins — CYP3A4 substrate interactions
    "simvastatin",
    "atorvastatin",
    "lovastatin",
    "rosuvastatin",
    "pravastatin",

    # CYP3A4 inhibitors — affect many other drugs (key for multi-hop, Phase 2)
    "fluconazole",
    "ketoconazole",
    "itraconazole",
    "ritonavir",
    "clarithromycin",
    "erythromycin",

    # CYP inducers — reduce efficacy of many drugs
    "rifampin",
    "carbamazepine",
    "phenytoin",
    "phenobarbital",
    "St. John's Wort",  # included because it's a common OTC inducer

    # Antiepileptics
    "valproate",
    "lamotrigine",
    "levetiracetam",
    "topiramate",

    # Cardiac — narrow therapeutic index
    "digoxin",
    "amiodarone",
    "metoprolol",
    "atenolol",
    "propranolol",
    "verapamil",
    "diltiazem",
    "amlodipine",
    "lisinopril",
    "losartan",
    "furosemide",
    "spironolactone",

    # Immunosuppressants — important for Phase 2 multi-hop
    "cyclosporine",
    "tacrolimus",
    "methotrexate",
    "azathioprine",

    # Antibiotics
    "ciprofloxacin",
    "metronidazole",
    "doxycycline",
    "azithromycin",
    "trimethoprim",
    "linezolid",

    # Opioids — CYP interactions + serotonin syndrome risk
    "methadone",
    "fentanyl",
    "tramadol",
    "oxycodone",
    "morphine",

    # Diabetes medications
    "metformin",
    "glipizide",
    "glibenclamide",
    "insulin",
    "sitagliptin",

    # PPIs / H2 blockers
    "omeprazole",
    "esomeprazole",
    "cimetidine",

    # Antipsychotics
    "haloperidol",
    "quetiapine",
    "clozapine",
    "risperidone",
    "olanzapine",

    # Benzodiazepines
    "diazepam",
    "alprazolam",
    "lorazepam",
    "midazolam",

    # Antiretrovirals
    "lopinavir",
    "efavirenz",
    "tenofovir",

    # Misc high-interaction drugs
    "lithium",
    "theophylline",
    "colchicine",
    "sildenafil",
    "tizanidine",
    "clonidine",
]
