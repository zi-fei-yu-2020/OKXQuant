"""A07 plugin language: acceptance, negative capabilities and bounded resources."""
import ast
import copy
import math
from pathlib import Path
import unittest
from unittest.mock import patch
from scripts import rule_interpreter as rules

ROOT=Path(__file__).resolve().parents[1]


def code(body, prefix=""):
    return prefix+"def check_risk(package, decision, context):\n"+"\n".join("    "+line for line in body.splitlines())+"\n"


class RuleLanguageTests(unittest.TestCase):
    def run_rule(self, source, package=None, decision=None, context=None, **kwargs):
        return rules.validate_rule(source).execute(package or {},decision or {},context or {},**kwargs)

    def test_actual_frontend_new_rule_template_is_supported(self):
        source=(ROOT/'okxquant_frontend/src/views/admin/InterceptorsPage.vue').read_text(encoding='utf-8')
        template=source.split('newCode.value = `',1)[1].split('`',1)[0]
        program=rules.validate_rule(template)
        for action in ('WAIT','BUY_LONG','SELL_SHORT'):
            self.assertEqual(program.execute({}, {'action':action}, {}),(True,''))

    def test_custom_strategy_uses_math_loops_try_unpacked_dict_and_fstrings(self):
        source=code('''action = str(decision.get('action', 'WAIT')).strip().upper()
if action == 'WAIT':
    return True, ''
try:
    samples = [float(x) for x in package.get('samples', [])]
    mean = sum(samples) / len(samples)
except (ValueError, TypeError, ZeroDivisionError):
    return False, 'invalid samples'
energy = 0.0
for value in samples:
    if value < 0:
        continue
    energy += math.sqrt(abs(value))
score = max(0, min(100, round(mean, 2)))
for name, value in {'a': 1, 'b': 2}.items():
    score += value
if not isinstance(package.get('samples'), (list, tuple)):
    return False, 'wrong data type'
return score >= 5 and math.isfinite(energy), f'{action.lower()} {score:.2f} {energy:.3f}' ''','import math\n')
        self.assertEqual(self.run_rule(source,{'samples':[1,4,9]},{'action':' buy_long '}),(True,'buy_long 7.67 6.000'))
        self.assertEqual(self.run_rule(source,{'samples':['bad']},{'action':'BUY_LONG'}),(False,'invalid samples'))

    def test_string_data_methods_and_safe_collection_conversions(self):
        source=code("""text = str(package.get('text', '')).strip().lower()
words = text.replace('!', '').split(' ')
ordered = sorted(set(words))
if text.startswith('alpha') and text.endswith('!'):
    return True, ','.join(ordered)
return False, str(len(words))""")
        self.assertEqual(self.run_rule(source,{'text':' Alpha beta alpha! '}),(True,'alpha,beta'))

    def test_short_circuit_semantics_avoid_unreached_data_errors(self):
        self.assertEqual(self.run_rule(code("return True or 1/0, ''")),(True,''))
        self.assertEqual(self.run_rule(code("return any(1/x > 0 for x in [1,0]), ''")),(True,''))
        self.assertEqual(self.run_rule(code("return all(x > 0 and 1/x > 0 for x in [0,1]), ''")),(False,''))

    def test_bounded_while_break_and_loop_else(self):
        self.assertEqual(self.run_rule(code('''i=0
while i<10:
    i+=1
    if i==3:
        break
else:
    return False, 'unexpected'
return i==3, str(i)''')),(True,'3'))

    def test_inputs_are_copied_and_not_mutated(self):
        p={'nested':[1,2]};d={'action':'BUY_LONG'};c={'active_inst_ids':{'A'}}
        before=copy.deepcopy((p,d,c))
        self.assertEqual(self.run_rule(code("package = {'new': 1}\nreturn True, ''"),p,d,c),(True,''))
        self.assertEqual((p,d,c),before)

    def test_exception_handler_catches_data_errors_only(self):
        self.assertEqual(self.run_rule(code("try:\n    x=float('invalid')\nexcept (ValueError,TypeError):\n    return False, 'bad'\nreturn True,''")),(False,'bad'))
        for body in ("return True", "return [True, '']", "return 1, ''", "return True, 4", "pass"):
            with self.subTest(body=body),self.assertRaises(rules.RuleError):self.run_rule(code(body))

    def test_legacy_external_imports_are_only_translated_as_exact_known_data_idiom(self):
        source=(ROOT/'plugins/interceptors/03_adx_volatility_filter.py').read_text(encoding='utf-8')
        with self.assertRaises(rules.RuleError):rules.validate_rule(source)
        program=rules.validate_rule(source,migrate_legacy=True)
        self.assertTrue(program.requires_plans)
        self.assertNotIn('from scripts.',program.source)
        self.assertFalse(any(isinstance(n,ast.ImportFrom) for n in ast.walk(program._tree)))
        altered=source.replace('catalog(package,vars(load_policy()))', 'catalog(decision,vars(load_policy()))')
        with self.assertRaises(rules.RuleError):rules.validate_rule(altered,migrate_legacy=True)


class RuleCapabilityDenialTests(unittest.TestCase):
    def test_forbidden_syntax_and_capabilities_are_rejected_at_validation(self):
        bodies=[
            "import os\nreturn True,''", "from pathlib import Path\nreturn True,''",
            "import math as os\nreturn True,''", "from math import sqrt\nreturn True,''",
            "return bool(open('/etc/passwd')), ''", "return bool(eval('1')), ''",
            "exec('x=1')\nreturn True,''", "return bool(__import__('os')), ''",
            "return bool(package.__class__), ''", "return bool((1).__class__.__mro__), ''",
            "return bool(getattr(package, 'get')), ''", "return bool(globals()), ''",
            "return bool(context.get('fn')()), ''", "return bool(context['fn']()), ''",
            "function = float\nreturn True,''", "return check_risk(package,decision,context)",
            "return bool('x'.format_map(context)), ''", "return bool('{0.__class__}'.format(1)), ''",
            "package['x'] = 1\nreturn True,''", "package.update({'x': 1})\nreturn True,''",
            "return bool(math.factorial(999999)), ''", "return bool(math.__dict__), ''",
            "class Evil:\n    pass\nreturn True,''", "def helper():\n    return True\nreturn helper(),''",
            "with open('x') as f:\n    pass\nreturn True,''", "return (lambda: True)(), ''",
            "yield True, ''", "raise Exception('bad')", "del package\nreturn True,''",
            "try:\n    return True,''\nexcept Exception as e:\n    return True,str(e)",
            "try:\n    return True,''\nfinally:\n    pass",
            "return bool([x for x in [1] for y in [2]]), ''",
        ]
        for body in bodies:
            with self.subTest(body=body),self.assertRaises(rules.RuleError):rules.validate_rule(code(body))
        for source in ("open('x','w')\n"+code("return True,''"),
                       "@open('x')\n"+code("return True,''"),
                       "def check_risk(package=open('x'), decision={}, context={}):\n    return True,''",
                       "def check_risk(package: open('x'), decision, context):\n    return True,''"):
            with self.subTest(source=source),self.assertRaises(rules.RuleError):rules.validate_rule(source)

    def test_no_arbitrary_object_protocol_or_callable_is_invoked(self):
        called=[]
        class Evil:
            def __getattribute__(self, name): called.append(name);raise AssertionError('object protocol invoked')
            def __str__(self): called.append('str');raise AssertionError('object protocol invoked')
            def __float__(self): called.append('float');raise AssertionError('object protocol invoked')
            def __iter__(self): called.append('iter');raise AssertionError('object protocol invoked')
        class EvilDict(dict):
            def items(self): called.append('items');raise AssertionError('dict subclass invoked')
        program=rules.validate_rule(code("return True, ''"))
        for value in (Evil(),EvilDict(),lambda:called.append('callable')):
            with self.assertRaises(rules.RuleError):program.execute({'evil':value},{},{})
        self.assertEqual(called,[])

    def test_input_cycles_are_rejected(self):
        value=[];value.append(value)
        with self.assertRaises(rules.RuleError):rules.copy_data({'cycle':value})

    def test_rule_execution_never_uses_host_python_execution_or_import(self):
        program=rules.validate_rule(code("return math.isfinite(float('1')), 'ok'",'import math\n'))
        with patch('builtins.eval',side_effect=AssertionError('eval')),patch('builtins.exec',side_effect=AssertionError('exec')),patch('builtins.__import__',side_effect=AssertionError('import')):
            self.assertEqual(program.execute({}, {}, {}),(True,'ok'))

    def test_budget_cannot_be_swallowed_by_except_exception(self):
        attacks=["while True:\n    pass", "x='x'*1000000000", "x=[0]*1000000000",
                 "x=2**1000000000", "x=list(range(1000000000))", "x=f'{1:9999999999f}'",
                 "x='x'.replace('', 'y'*8192)", "x=('x'*4096).join(['a']*1000)",
                 "x='%1000000000s' % 'x'", "x=round(1.25, 1000000000)"]
        for body in attacks:
            wrapped="try:\n"+'\n'.join('    '+line for line in body.splitlines())+"\nexcept Exception:\n    return True, 'swallowed'\nreturn True,''"
            # Unsupported operations (e.g. string %-format) may be caught as ordinary
            # type errors, but must never allocate according to attacker width.
            if "%1000000000s" in body:
                self.assertEqual(rules.validate_rule(code(wrapped)).execute({},{},{}),(True,'swallowed'))
            else:
                with self.subTest(body=body),self.assertRaises(rules.RuleError):rules.validate_rule(code(wrapped)).execute({},{},{})

    def test_source_ast_input_and_step_budgets(self):
        for source in ('#'*(rules.MAX_SOURCE_BYTES+1),code('x='+repr([0]*6500)+"\nreturn True,''")):
            with self.assertRaises(rules.RuleError):rules.validate_rule(source)
        program=rules.validate_rule(code("return True,''"))
        for value in ({'x':'x'*(rules.MAX_STRING+1)}, {'x':[1]*(rules.MAX_ITEMS+1)}, {'x':2**300}):
            with self.assertRaises(rules.RuleError):program.execute(value,{}, {})
        deep={}
        for _ in range(40):deep={'x':deep}
        with self.assertRaises(rules.RuleError):program.execute(deep,{}, {})
        with self.assertRaises(rules.RuleBudgetExceeded):program.execute({}, {}, {},rules.Budget(steps=0))

    def test_nested_repetition_cannot_amplify_memory_unboundedly(self):
        source=code("a=[0]\nfor i in range(100):\n    a=[a,a]\nreturn True,str(a)")
        with self.assertRaises(rules.RuleError):rules.validate_rule(source).execute({}, {}, {})

class AdditionalBudgetBoundaryTests(unittest.TestCase):
    def test_numeric_format_and_container_budget_matrix(self):
        attacks=["x=int('9'*1000)","x=2**255 * 2**255", "x=[1]*4097", "x='a'*8193",
                 "x=f'{1:.99f}'", "x=f'{1:999f}'", "x=tuple(range(2001))"]
        for body in attacks:
            with self.subTest(body=body),self.assertRaises(rules.RuleError):
                rules.validate_rule(code(body+"\nreturn True,''")).execute({}, {}, {})

    def test_pipeline_style_budget_is_shared_across_rules_not_reset_per_execution(self):
        program=rules.validate_rule(code("x=0\nfor i in range(10):\n    x+=i\nreturn True,''"))
        shared=rules.Budget(loops=15)
        self.assertEqual(program.execute({}, {}, {},shared),(True,''))
        with self.assertRaises(rules.RuleBudgetExceeded):program.execute({}, {}, {},shared)

    def test_expired_budget_fails_at_next_interpreter_step(self):
        program=rules.validate_rule(code("return True,''"))
        budget=rules.Budget();budget.deadline=0
        with self.assertRaises(rules.RuleBudgetExceeded):program.execute({}, {}, {},budget)

    def test_supported_math_and_container_subsets_do_not_expose_attributes(self):
        body="data={'x':[-1,2,3]}\nresult={str(x): abs(x) for x in data.get('x')}\nreturn math.isfinite(sum(result.values())) and data['x'][1:] == [2,3], str(min(result.values()))"
        self.assertEqual(rules.validate_rule(code(body,'import math\n')).execute({}, {}, {}),(True,'1'))

    def test_reserved_data_demand_is_not_catchable_and_never_passes_a_loader_to_rules(self):
        program=rules.validate_rule(code("try:\n    plans=context.get('_validated_entry_plans', [])\nexcept Exception:\n    return True,'swallowed'\nreturn bool(plans),''"))
        with self.assertRaises(rules.RuleDataRequired):program.execute({}, {}, {})
        self.assertEqual(program.execute({}, {}, {rules.PLAN_KEY:[]}),(False,''))

    def test_total_text_budget_applies_even_to_plain_data_copy_without_runtime_budget(self):
        with self.assertRaises(rules.RuleBudgetExceeded):rules.copy_data({'text':['x'*8192]*128})
