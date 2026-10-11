from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from catalog_card_text import repair_texts, missing_cost


class CardTextRepairTest(unittest.TestCase):
    def test_fills_missing_cost_and_records_provenance(self):
        ru, en, evidence = repair_texts("JAIL_200", "Призывает существ за ( ).", "Summon -Cost minions.",
                                      "[x]Призывает существ\nза (2).", "[x]Summon 2-Cost\nminions.")
        self.assertIn("(2)", ru)
        self.assertIn("2-Cost", en)
        self.assertEqual(set(evidence), {"ru", "en"})

    def test_preserves_valid_newer_text(self):
        self.assertEqual(repair_texts("CARD", "за (2)", "Summon 2-Cost", "за (3)", "Summon 3-Cost")[:2],
                         ("за (2)", "Summon 2-Cost"))

    def test_retains_unresolved_template_instead_of_inventing_number(self):
        ru, en, _ = repair_texts("MEND_502", "за ( )", "Summon -Cost", "за ({0})", "Summon {0}-Cost")
        self.assertIn("{0}", ru)
        self.assertIn("{0}", en)
        self.assertFalse(missing_cost(en))

    def test_no_evidence_leaves_input_unchanged(self):
        self.assertEqual(repair_texts("CARD", "за ( )", "-Cost", None, None), ("за ( )", "-Cost", {}))

    def test_fixes_only_confirmed_localized_hotfix(self):
        self.assertIn("(2)", repair_texts("TLC_600", "стоит на (3) меньше", "Costs (2) less.",
                                          "[x]стоит на (2) меньше", "Costs (2) less.")[0])
        self.assertIn("(3)", repair_texts("TLC_600", "стоит на (3) меньше", "Costs (1) less.",
                                          "стоит на (2) меньше", "Costs (2) less.")[0])

    def test_valid_costs_are_not_classified_as_missing(self):
        for text in ("Summon 1-Cost", "Summon {0}-Cost", "за (0)", "за ({0})"):
            self.assertFalse(missing_cost(text))
