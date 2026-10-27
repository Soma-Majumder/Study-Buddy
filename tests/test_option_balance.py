import copy
import unittest
from study_buddy.option_balance import option_balance_issues


def candidate(correct='a', text='x'*13):
    return dict(quiz=dict(questions=[dict(position=1, options=dict(a=text,b='y'*10,c='z'*10,d='w'*10))]),
                answer_key=[dict(position=1,correct_option=correct)])


class OptionBalanceTests(unittest.TestCase):
    def test_boundary_and_flag_above_threshold_without_private_content(self):
        self.assertEqual(option_balance_issues(candidate()), [])
        flagged=option_balance_issues(candidate(text='x'*14))
        self.assertEqual(flagged,[dict(position=1,code='option_length_flag',ratio=1.4)])

    def test_private_key_drives_comparison_without_mutating_input(self):
        c=candidate(correct='b',text='x'*40);before=copy.deepcopy(c)
        self.assertEqual(option_balance_issues(c),[])
        self.assertEqual(c,before)

    def test_invalid_threshold_and_incomplete_key_are_not_passes(self):
        for threshold in (1,float('nan'),float('inf'),True):
            with self.assertRaises(ValueError):option_balance_issues(candidate(),threshold)
        c=candidate();c['answer_key']=[]
        with self.assertRaises(ValueError):option_balance_issues(c)
        c=candidate();c['quiz']['questions'][0]['options']['a']=' '
        with self.assertRaises(ValueError):option_balance_issues(c)
