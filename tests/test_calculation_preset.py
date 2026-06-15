# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError

class TestCalculationPreset(TransactionCase):

    def setUp(self):
        super(TestCalculationPreset, self).setUp()
        self.Preset = self.env['calculation.preset']

    def test_translate_to_sql_basic(self):
        """Test basic arithmetic translation."""
        formula = "({rev} - {cost}) / {cost} * 100"
        bindings = {
            'rev': '"sale_order"."amount_total"',
            'cost': '1000'
        }
        sql = self.Preset.translate_to_sql(formula, bindings)
        self.assertIn('"sale_order"."amount_total"', sql)
        self.assertIn(' - 1000', sql)
        self.assertIn('NULLIF((1000), 0)', sql)
        self.assertIn(' * 100', sql)

    def test_translate_to_sql_conditional(self):
        """Test conditional expression (IF/THEN/ELSE) translation."""
        formula = "if({rev} > 1000, {rev} * 0.1, 0)"
        bindings = {
            'rev': '"sale_order"."amount_total"'
        }
        sql = self.Preset.translate_to_sql(formula, bindings)
        self.assertIn('CASE WHEN', sql)
        self.assertIn('THEN', sql)
        self.assertIn('ELSE', sql)
        self.assertIn('"sale_order"."amount_total" > 1000', sql)

    def test_translate_to_sql_safety(self):
        """Test that unauthorized functions are blocked."""
        formula = "__import__('os').system('ls')"
        bindings = {}
        with self.assertRaises(UserError):
            self.Preset.translate_to_sql(formula, bindings)

    def test_translate_to_sql_safety_complex(self):
        """Test that unauthorized method calling is blocked."""
        formula = "{rev}.pop()"
        bindings = {'rev': 'some_table.col'}
        with self.assertRaises(UserError):
            self.Preset.translate_to_sql(formula, bindings)
    def test_translate_to_sql_aggregates(self):
        """Test aggregation functions in formulas."""
        formula = "sum({rev}) - sum({tax})"
        bindings = {
            'rev': '"sale_order"."amount_total"',
            'tax': '"sale_order"."amount_tax"'
        }
        sql = self.Preset.translate_to_sql(formula, bindings)
        self.assertIn('SUM("sale_order"."amount_total")', sql)
        self.assertIn('SUM("sale_order"."amount_tax")', sql)

    def test_translate_to_sql_min_max(self):
        """Test min/max aggregate vs scalar mapping."""
        # Aggregate min
        sql_agg = self.Preset.translate_to_sql("min({a})", {'a': 'col'})
        self.assertIn('MIN(col)', sql_agg)
        
        # Scalar least (min with >1 arg)
        sql_scalar = self.Preset.translate_to_sql("min({a}, 0)", {'a': 'col'})
        self.assertIn('LEAST(col, 0)', sql_scalar)

        # Aggregate max
        sql_agg_max = self.Preset.translate_to_sql("max({a})", {'a': 'col'})
        self.assertIn('MAX(col)', sql_agg_max)
        
        # Scalar greatest (max with >1 arg)
        sql_scalar_max = self.Preset.translate_to_sql("max({a}, 100)", {'a': 'col'})
        self.assertIn('GREATEST(col, 100)', sql_scalar_max)

    def test_translate_to_sql_extra_funcs(self):
        """Test extra functions COALESCE, NULLIF."""
        sql = self.Preset.translate_to_sql("coalesce({a}, 0)", {'a': 'col'})
        self.assertIn('COALESCE(col, 0)', sql)
        
        sql_nullif = self.Preset.translate_to_sql("nullif({a}, '')", {'a': 'col'})
        self.assertIn("NULLIF(col, '')", sql_nullif)

    def test_translate_to_sql_division(self):
        """Test division with float casting and NULLIF."""
        sql = self.Preset.translate_to_sql("{a} / {b}", {'a': '1', 'b': '2'})
        self.assertIn('::float', sql)
        self.assertIn('NULLIF', sql)
        # Expected roughly: ((1)::float / NULLIF((2), 0))

    def test_system_preset_protection(self):
        """Test that system presets cannot be deleted."""
        system_preset = self.Preset.create({
            'name': 'System Preset',
            'formula': '{a}',
            'is_system': True,
        })
        with self.assertRaises(UserError):
            system_preset.unlink()

    def test_system_preset_copy(self):
        """Test that copying a system preset results in a non-system preset."""
        system_preset = self.Preset.create({
            'name': 'System Preset',
            'formula': '{a}',
            'is_system': True,
        })
        copy_preset = system_preset.copy()
        self.assertFalse(copy_preset.is_system)
        self.assertNotEqual(system_preset.id, copy_preset.id)
        # Copy should be deletable
        copy_preset.unlink()

    def test_translate_to_sql_advanced_merging(self):
        """Test that compatible aggregations are merged in additions."""
        configs = [
            {'name': 'this_month', 'column': 'amount', 'aggregate': 'SUM', 'filter_domain': 'date >= "2024-01-01"'},
            {'name': 'last_month', 'column': 'amount', 'aggregate': 'SUM', 'filter_domain': 'date >= "2023-12-01" AND date < "2024-01-01"'},
            {'name': 'other_col', 'column': 'qty', 'aggregate': 'SUM', 'filter_domain': '1=1'},
        ]
        
        # 1. Simple addition - Should merge
        formula = "{this_month} + {last_month}"
        sql = self.Preset.translate_to_sql_advanced(formula, configs)
        self.assertIn('SUM(amount) FILTER (WHERE (date >= "2024-01-01") OR (date >= "2023-12-01" AND date < "2024-01-01"))', sql)
        self.assertEqual(sql.count('SUM(amount)'), 1)

        # 2. Subtraction - Should NOT merge
        formula_sub = "{this_month} - {last_month}"
        sql_sub = self.Preset.translate_to_sql_advanced(formula_sub, configs)
        self.assertIn('SUM(amount) FILTER (WHERE (date >= "2024-01-01"))', sql_sub)
        self.assertIn('SUM(amount) FILTER (WHERE (date >= "2023-12-01" AND date < "2024-01-01"))', sql_sub)
        self.assertIn(' - ', sql_sub)

        # 3. Different columns - Should NOT merge
        formula_diff = "{this_month} + {other_col}"
        sql_diff = self.Preset.translate_to_sql_advanced(formula_diff, configs)
        self.assertIn('SUM(amount)', sql_diff)
        self.assertIn('SUM(qty)', sql_diff)
        self.assertIn(' + ', sql_diff)

        # 4. Triple addition - Should merge all
        formula_triple = "{this_month} + {last_month} + {this_month}"
        sql_triple = self.Preset.translate_to_sql_advanced(formula_triple, configs)
        self.assertIn('SUM(amount) FILTER (WHERE (date >= "2024-01-01") OR (date >= "2023-12-01" AND date < "2024-01-01") OR (date >= "2024-01-01"))', sql_triple)
        self.assertEqual(sql_triple.count('SUM(amount)'), 1)
