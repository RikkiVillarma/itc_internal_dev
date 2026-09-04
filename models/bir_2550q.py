import re
from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
from datetime import datetime
import base64
import io
import os



class Bir2550Q(models.Model):
    _name = 'bir.2550q'
    _description = 'Quarterly Value-Added Tax (VAT) Return'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    # ── Sequence / Reference ────────────────────────────────────────────
    name = fields.Char(
        string='Reference', required=True, copy=False,
        readonly=True, default='New'
    )
    company_id = fields.Many2one(
        'res.company', string='Company', required=True,
        default=lambda self: self.env.company
    )
    currency_id = fields.Many2one(
        'res.currency', related='company_id.currency_id', readonly=True
    )
    state = fields.Selection([
        ('draft',      'Draft'),
        ('confirmed',  'Confirmed'),
        ('filed',      'Filed'),
        ('cancelled',  'Cancelled'),
    ], string='Status', default='draft', tracking=True)

    # ── Part I – Background Information ─────────────────────────────────
    year_ended = fields.Char(
        string='Year Ended (MM/YYYY)', required=True,
        help='Format: MM/YYYY e.g. 12/2024'
    )
    quarter = fields.Selection([
        ('1', '1st'), ('2', '2nd'), ('3', '3rd'), ('4', '4th')
    ], string='Quarter', required=True)
    return_period_from = fields.Date(string='Return Period From', readonly=True)
    return_period_to   = fields.Date(string='Return Period To',   readonly=True)
    is_amended         = fields.Boolean(string='Amended Return?')
    is_short_period    = fields.Boolean(string='Short Period Return?')
    taxpayer_classification = fields.Selection([
        ('micro', 'Micro'), ('small', 'Small'),
        ('medium', 'Medium'), ('large', 'Large'),
    ], string='Taxpayer Classification')
    is_tax_relief      = fields.Boolean(string='Availing Tax Relief?')
    tax_relief_specify = fields.Char(string='Tax Relief Details')

    # ── Part IV – Output Tax ─────────────────────────────────────────────
    vatable_sales  = fields.Monetary(string='31A VATable Sales',  readonly=True)
    output_tax_31b = fields.Monetary(string='31B Output Tax',     readonly=True)
    zero_rated_sales = fields.Monetary(string='32 Zero-Rated Sales', readonly=True)
    exempt_sales     = fields.Monetary(string='33 Exempt Sales',     readonly=True)
    total_sales_34a  = fields.Monetary(string='34A Total Sales',      compute='_compute_totals', store=True)
    total_output_34b = fields.Monetary(string='34B Total Output Tax', compute='_compute_totals', store=True)
    output_vat_uncollected_35  = fields.Monetary(string='35 Output VAT on Uncollected Receivables')
    output_vat_recovered_36    = fields.Monetary(string='36 Output VAT on Recovered Uncollected Receivables')
    total_adjusted_output_tax  = fields.Monetary(string='37 Total Adjusted Output Tax Due', compute='_compute_totals', store=True)

    # ── Part IV – Allowable Input Tax (Items 38–43) ──────────────────────
    input_tax_carried_over     = fields.Monetary(string='38 Input Tax Carried Over from Previous Quarter')
    input_tax_deferred_39      = fields.Monetary(string='39 Input Tax Deferred on Capital Goods >P1M')
    transitional_input_tax     = fields.Monetary(string='40 Transitional Input Tax')
    presumptive_input_tax      = fields.Monetary(string='41 Presumptive Input Tax')
    other_input_tax_42         = fields.Monetary(string='42 Other Input Tax')
    total_prior_input_tax_43   = fields.Monetary(string='43 Total Prior Input Tax', compute='_compute_totals', store=True)

    # ── Part IV – Current Transactions (Items 44–50) ─────────────────────
    domestic_purchases_44a     = fields.Monetary(string='44A Domestic Purchases',          readonly=True)
    domestic_input_tax_44b     = fields.Monetary(string='44B Domestic Input Tax',          readonly=True)
    nonresident_services_45a   = fields.Monetary(string='45A Services by Non-Residents',   readonly=True)
    nonresident_input_tax_45b  = fields.Monetary(string='45B Non-Resident Input Tax',      readonly=True)
    importations_46a           = fields.Monetary(string='46A Importations',                readonly=True)
    importations_input_tax_46b = fields.Monetary(string='46B Importations Input Tax',      readonly=True)
    other_purchases_47a        = fields.Monetary(string='47A Other Purchases',             readonly=True)
    other_input_tax_47b        = fields.Monetary(string='47B Other Input Tax',             readonly=True)
    domestic_no_input_48       = fields.Monetary(string='48 Domestic Purchases No Input Tax', readonly=True)
    vat_exempt_importations_49 = fields.Monetary(string='49 VAT-Exempt Importations',      readonly=True)
    total_current_purchases    = fields.Monetary(string='50A Total Current Purchases',     readonly=True)
    total_current_input_tax    = fields.Monetary(string='50B Total Current Input Tax',     readonly=True)
    total_available_input_tax  = fields.Monetary(string='51 Total Available Input Tax',    compute='_compute_totals', store=True)

    # ── Part IV – Deductions from Input Tax (Items 52–59) ────────────────
    capital_goods_deferred_52  = fields.Monetary(string='52 Input Tax on Capital Goods Deferred')
    input_tax_exempt_sales_53  = fields.Monetary(string='53 Input Tax Attributable to VAT Exempt Sales')
    vat_refund_tcc_54          = fields.Monetary(string='54 VAT Refund/TCC Claimed')
    input_vat_unpaid_55        = fields.Monetary(string='55 Input VAT on Unpaid Payables')
    other_deductions_56        = fields.Monetary(string='56 Other Deductions')
    total_deductions_57        = fields.Monetary(string='57 Total Deductions from Input Tax', compute='_compute_totals', store=True)
    input_vat_settled_58       = fields.Monetary(string='58 Input VAT on Settled Unpaid Payables')
    adjusted_deductions_59     = fields.Monetary(string='59 Adjusted Deductions from Input Tax', compute='_compute_totals', store=True)
    total_allowable_input_tax  = fields.Monetary(string='60 Total Allowable Input Tax',    compute='_compute_totals', store=True)
    net_vat_payable            = fields.Monetary(string='61 / 15 Net VAT Payable/(Excess Input Tax)', compute='_compute_totals', store=True)

    # ── Part II – Tax Credits / Payments ─────────────────────────────────
    creditable_vat_withheld    = fields.Monetary(string='16 Creditable VAT Withheld')
    advance_vat_payments       = fields.Monetary(string='17 Advance VAT Payments')
    vat_paid_prev_return_18    = fields.Monetary(string='18 VAT Paid in Previously Filed Return')
    other_credits_19           = fields.Monetary(string='19 Other Credits/Payment')
    total_tax_credits_20       = fields.Monetary(string='20 Total Tax Credits/Payment',    compute='_compute_totals', store=True)
    tax_still_payable_21       = fields.Monetary(string='21 Tax Still Payable/(Excess Credits)', compute='_compute_totals', store=True)

    # ── Part II – Penalties ───────────────────────────────────────────────
    surcharge_22    = fields.Monetary(string='22 Surcharge')
    interest_23     = fields.Monetary(string='23 Interest')
    compromise_24   = fields.Monetary(string='24 Compromise')
    total_penalties = fields.Monetary(string='25 Total Penalties', compute='_compute_totals', store=True)
    total_amount_payable = fields.Monetary(string='26 Total Amount Payable/(Excess Credits)', compute='_compute_totals', store=True)
    
    
    
    @api.constrains('year_ended')
    def _check_year_ended_format(self):
        pattern = re.compile(r'^(0[1-9]|1[0-2])/\d{4}$')
        for record in self:
            if record.year_ended and not pattern.match(record.year_ended):
                raise ValidationError(
                    "Year Ended must be in MM/YYYY format (e.g. 12/2024)."
                )

    # ====================================================================
    # Sequence
    # ====================================================================
    @api.model
    def create(self, vals):
        if vals.get('name', 'New') == 'New':
            vals['name'] = self.env['ir.sequence'].next_by_code('bir.2550q') or 'New'
        return super().create(vals)

    # ====================================================================
    # Auto-fill return period dates from quarter + year
    # ====================================================================
    @api.onchange('quarter', 'year_ended')
    def _onchange_quarter_year(self):
        for rec in self:
            if not rec.year_ended or not rec.quarter:
                continue
            try:
                year = int(rec.year_ended.split('/')[-1])
            except (ValueError, IndexError):
                continue
            quarters = {
                '1': (f'{year}-01-01', f'{year}-03-31'),
                '2': (f'{year}-04-01', f'{year}-06-30'),
                '3': (f'{year}-07-01', f'{year}-09-30'),
                '4': (f'{year}-10-01', f'{year}-12-31'),
            }
            date_from, date_to = quarters[rec.quarter]
            rec.return_period_from = date_from
            rec.return_period_to   = date_to

    # ====================================================================
    # Computed totals
    # ====================================================================
    @api.depends(
        'vatable_sales', 'zero_rated_sales', 'exempt_sales',
        'output_tax_31b',
        'output_vat_uncollected_35', 'output_vat_recovered_36',
        'input_tax_carried_over', 'input_tax_deferred_39',
        'transitional_input_tax', 'presumptive_input_tax', 'other_input_tax_42',
        'total_current_input_tax',
        'capital_goods_deferred_52', 'input_tax_exempt_sales_53',
        'vat_refund_tcc_54', 'input_vat_unpaid_55', 'other_deductions_56',
        'input_vat_settled_58',
        'creditable_vat_withheld', 'advance_vat_payments',
        'vat_paid_prev_return_18', 'other_credits_19',
        'surcharge_22', 'interest_23', 'compromise_24',
    )
    def _compute_totals(self):
        for rec in self:
            rec.total_sales_34a  = (rec.vatable_sales or 0) + (rec.zero_rated_sales or 0) + (rec.exempt_sales or 0)
            rec.total_output_34b = rec.output_tax_31b or 0
            rec.total_adjusted_output_tax = (
                (rec.total_output_34b or 0)
                - (rec.output_vat_uncollected_35 or 0)
                + (rec.output_vat_recovered_36 or 0)
            )
            rec.total_prior_input_tax_43 = (
                (rec.input_tax_carried_over or 0)
                + (rec.input_tax_deferred_39 or 0)
                + (rec.transitional_input_tax or 0)
                + (rec.presumptive_input_tax or 0)
                + (rec.other_input_tax_42 or 0)
            )
            rec.total_available_input_tax = (
                (rec.total_prior_input_tax_43 or 0)
                + (rec.total_current_input_tax or 0)
            )
            rec.total_deductions_57 = (
                (rec.capital_goods_deferred_52 or 0)
                + (rec.input_tax_exempt_sales_53 or 0)
                + (rec.vat_refund_tcc_54 or 0)
                + (rec.input_vat_unpaid_55 or 0)
                + (rec.other_deductions_56 or 0)
            )
            rec.adjusted_deductions_59 = (
                (rec.total_deductions_57 or 0)
                + (rec.input_vat_settled_58 or 0)
            )
            rec.total_allowable_input_tax = (
                (rec.total_available_input_tax or 0)
                - (rec.adjusted_deductions_59 or 0)
            )
            rec.net_vat_payable = (
                (rec.total_adjusted_output_tax or 0)
                - (rec.total_allowable_input_tax or 0)
            )
            rec.total_tax_credits_20 = (
                (rec.creditable_vat_withheld or 0)
                + (rec.advance_vat_payments or 0)
                + (rec.vat_paid_prev_return_18 or 0)
                + (rec.other_credits_19 or 0)
            )
            rec.tax_still_payable_21 = (
                (rec.net_vat_payable or 0)
                - (rec.total_tax_credits_20 or 0)
            )
            rec.total_penalties = (
                (rec.surcharge_22 or 0)
                + (rec.interest_23 or 0)
                + (rec.compromise_24 or 0)
            )
            rec.total_amount_payable = (
                (rec.tax_still_payable_21 or 0)
                + (rec.total_penalties or 0)
            )

    # ====================================================================
    # Generate data from posted journal entries + account.payment
    # ====================================================================
    def action_generate_data(self):
        for rec in self:
            if not rec.return_period_from or not rec.return_period_to:
                rec._onchange_quarter_year()
            if not rec.return_period_from:
                raise UserError(_('Please set the Year Ended and Quarter first.'))

            date_from  = rec.return_period_from
            date_to    = rec.return_period_to
            company_id = rec.company_id.id

            # ── 1. OUTPUT VAT from posted sales invoices ─────────────────
            sales_lines = self.env['account.move.line'].search([
                ('company_id',        '=',  company_id),
                ('date',              '>=', date_from),
                ('date',              '<=', date_to),
                ('parent_state',      '=',  'posted'),
                ('tax_ids',           '!=', False),
                ('move_id.move_type', 'in', ['out_invoice', 'out_refund']),
            ])
            vatable_sales = output_tax = zero_rated = exempt = 0.0
            sign_map = {'out_invoice': 1, 'out_refund': -1}

            for line in sales_lines:
                sign = sign_map.get(line.move_id.move_type, 1)
                for tax in line.tax_ids:
                    amt   = line.price_subtotal * sign
                    group = (tax.tax_group_id.name or '').lower()

                    if tax.amount > 0:
                        vatable_sales += amt
                        output_tax    += amt * (tax.amount / 100)
                    elif 'zero' in group:
                        zero_rated += amt
                    elif 'exempt' in group:
                        exempt += amt

            # ── 2. INPUT VAT from posted purchase invoices ───────────────
            purchase_lines = self.env['account.move.line'].search([
                ('company_id',        '=',  company_id),
                ('date',              '>=', date_from),
                ('date',              '<=', date_to),
                ('parent_state',      '=',  'posted'),
                ('tax_ids',           '!=', False),
                ('move_id.move_type', 'in', ['in_invoice', 'in_refund']),
            ])
            domestic_purchases = domestic_input_tax = 0.0
            sign_map_p = {'in_invoice': 1, 'in_refund': -1}

            for line in purchase_lines:
                sign = sign_map_p.get(line.move_id.move_type, 1)
                amt  = line.price_subtotal * sign
                domestic_purchases += amt

                for tax in line.tax_ids:
                    if tax.amount > 0:
                        domestic_input_tax += amt * (tax.amount / 100)

            # ── 3. TAX CREDITS via JOURNAL ENTRIES ───────────────────────

            # 👉 CONFIG (VERY IMPORTANT — adjust these!)
            vat_payable_account = self.env['account.account'].search([
                ('name', 'ilike', 'VAT Payable'),
            ], limit=1)

            withholding_account = self.env['account.account'].search([
                ('name', 'ilike', 'Withholding'),
            ], limit=1)

            # ── Item 16: Creditable VAT Withheld ─────────────────────────
            creditable_vat_withheld = 0.0
            if withholding_account:
                withholding_lines = self.env['account.move.line'].search([
                    ('company_id', '=', company_id),
                    ('date', '>=', date_from),
                    ('date', '<=', date_to),
                    ('parent_state', '=', 'posted'),
                    ('account_id', '=', withholding_account.id),
                    ('credit', '>', 0),
                ])
                creditable_vat_withheld = sum(withholding_lines.mapped('credit'))

            # ── Item 17: Advance VAT Payments ────────────────────────────
            advance_vat_payments = 0.0
            if vat_payable_account:
                vat_lines = self.env['account.move.line'].search([
                    ('company_id', '=', company_id),
                    ('date', '>=', date_from),
                    ('date', '<=', date_to),
                    ('parent_state', '=', 'posted'),
                    ('account_id', '=', vat_payable_account.id),
                    ('debit', '>', 0),
                ])
                advance_vat_payments = sum(vat_lines.mapped('debit'))

            # ── Item 18: VAT Paid in Previous Return ─────────────────────
            from dateutil.relativedelta import relativedelta

            prev_quarter_end   = date_from - relativedelta(days=1)
            prev_quarter_start = prev_quarter_end - relativedelta(months=3) + relativedelta(days=1)

            vat_paid_prev_return = 0.0
            if vat_payable_account:
                prev_vat_lines = self.env['account.move.line'].search([
                    ('company_id', '=', company_id),
                    ('date', '>=', prev_quarter_start),
                    ('date', '<=', prev_quarter_end),
                    ('parent_state', '=', 'posted'),
                    ('account_id', '=', vat_payable_account.id),
                    ('debit', '>', 0),
                ])
                vat_paid_prev_return = sum(prev_vat_lines.mapped('debit'))

            # ── 4. WRITE ────────────────────────────────────────────────
            rec.write({
                'vatable_sales':            vatable_sales,
                'output_tax_31b':           output_tax,
                'zero_rated_sales':         zero_rated,
                'exempt_sales':             exempt,
                'domestic_purchases_44a':   domestic_purchases,
                'domestic_input_tax_44b':   domestic_input_tax,
                'total_current_purchases':  domestic_purchases,
                'total_current_input_tax':  domestic_input_tax,
                'creditable_vat_withheld':  creditable_vat_withheld,
                'advance_vat_payments':     advance_vat_payments,
                'vat_paid_prev_return_18':  vat_paid_prev_return,
            })

    # ====================================================================
    # Workflow
    # ====================================================================
    def action_confirm(self):
        self.write({'state': 'confirmed'})

    def action_file(self):
        self.write({'state': 'filed'})

    def action_cancel(self):
        self.write({'state': 'cancelled'})

    def action_set_draft(self):
        self.write({'state': 'draft'})

    # ====================================================================
    # PDF formatting helpers
    # ====================================================================

    def _fmt_amt(self, value):
        if not value:
            return ''
        return f'{value:,.2f}'

    def _format_tin_for_pdf(self, tin_raw):
        """
        Format TIN for the 2550Q PDF field.
        The PDF has printed '/' separators at fixed positions.
        We insert spaces at those positions so digits land in the correct boxes.
        TIN format: NNN-NNN-NNN-NNNNN → 'NNN NNN NNN NNNNN'
        Spaces skip the pre-printed slashes; 17 chars across 17 equal cells.
        Tc = field_width / 17 - font_size:
          Text4  (p1) w=241.2 / 17 = 14.19 → Tc = 4.96
          Text39 (p2) w=197.4 / 17 = 11.61 → Tc = 2.38
        """
        digits = (tin_raw or '').replace('-', '').replace(' ', '').replace('/', '')
        seg1 = digits[0:3]    # 3 digits before first /
        seg2 = digits[3:6]    # 3 digits before second /
        seg3 = digits[6:9]    # 3 digits before third /
        seg4 = digits[9:]     # branch code (up to 5 digits)
        return f'{seg1} {seg2} {seg3} {seg4}'

    def _format_date_for_pdf(self, d):
        """
        Format a Date for MM/DD/YYYY box fields (Text2, Text3).
        The PDF pre-draws '/' separators — we strip them, send 8 digit chars.
        8 cells → 'MMDDYYYY'
        Tc = field_width / 8 - font_size:
          Text2 w=112.9 / 8 = 14.11 → Tc = 4.88
          Text3 w=113.9 / 8 = 14.24 → Tc = 5.01
        """
        if not d:
            return ''
        if isinstance(d, str):
            try:
                d = datetime.strptime(d, '%Y-%m-%d').date()
            except ValueError:
                return ''
        return d.strftime('%m%d%Y')   # "01312026" — slashes are pre-drawn

    def _format_year_for_pdf(self, year_ended):
        """
        Format Year Ended (MM/YYYY) for its 6-box field (Text1).
        The PDF pre-draws the '/' separator — we strip it, send 6 digit chars.
        6 cells → 'MMYYYY'
        Tc = 85.4 / 6 - 9.23 = 5.00 (set in SPACED_FIELDS)
        """
        return (year_ended or '').replace('/', '').replace(' ', '')

    # ====================================================================
    # PDF field map  (verified against 2550Q April 2024 ENCS)
    # Field positions confirmed by extracting /Rect from PDF annotations.
    # ====================================================================
    def _build_pdf_field_map(self):
        self.ensure_one()
        c = self.company_id

        # ------------------------------------------------------------------
        # TIN: use _format_tin_for_pdf() — spaces at separator positions
        # so each digit lands in its own box over the pre-drawn "/" marks.
        # Output "NNN NNN NNN NNNNN" (17 chars), Tc set in SPACED_FIELDS.
        # ------------------------------------------------------------------
        tin_fmt = self._format_tin_for_pdf(c.vat or '')
        tin_p2  = tin_fmt   # same string, different Tc for narrower p2 field

        def amt(f):
            return self._fmt_amt(f)

        # ------------------------------------------------------------------
        # Helper: mark a checkbox with 'X' (True) or leave blank (False)
        # ------------------------------------------------------------------
        def chk(condition):
            return 'X' if condition else ''

        field_map = {

            # ==============================================================
            # PAGE 1
            # ==============================================================

            # ── Row 1 – Calendar/Fiscal + Year Ended + Quarter checkboxes ─
            # Text1  (x=304, y=900, w=85.4) → "2 Year Ended (MM/YYYY)"
            # 6 individual boxes — PDF pre-draws "/" between MM and YYYY.
            # Send 6 digits "MMYYYY" (no slash). Tc=5.00 in SPACED_FIELDS.
            'Text1':  self._format_year_for_pdf(self.year_ended),

            # Text7  (x=74,  y=899) → Calendar checkbox (always Calendar for now)
            'Text7':  'X',
            # Text12 (x=141, y=899) → Fiscal checkbox
            'Text12': '',

            # Quarter checkboxes — only one is 'X'
            # Text13 (x=434, y=899) → 1st Quarter
            'Text13': chk(self.quarter == '1'),
            # Text14 (x=472, y=899) → 2nd Quarter
            'Text14': chk(self.quarter == '2'),
            # Text15 (x=511, y=899) → 3rd Quarter
            'Text15': chk(self.quarter == '3'),
            # Text16 (x=554, y=899) → 4th Quarter
            'Text16': chk(self.quarter == '4'),

            # ── Row 2 – Return Period + Amended + Short Period ─────────────
            # Text2  (x=64, y=872, w=112.9) → "4 Return Period From (MM/DD/YYYY)"
            # 8 individual boxes — PDF pre-draws "/" separators.
            # Send 8 digits "MMDDYYYY". Tc=4.88 in SPACED_FIELDS.
            'Text2':  self._format_date_for_pdf(self.return_period_from),
            # Text3  (x=204, y=871, w=113.9) → "Return Period To (MM/DD/YYYY)"
            # 8 individual boxes — same structure as Text2. Tc=5.01.
            'Text3':  self._format_date_for_pdf(self.return_period_to),

            # Items 5 & 6 share the same y-row. Left-to-right x order:
            #   346 → 390 → 477 → 533
            # Item 5 "Amended Return?" occupies the LEFT half (x≈346–404):
            # Text22 (x=346, y=870) → "5 Amended Return? – Yes" checkbox
            'Text22': chk(self.is_amended),
            # Text21 (x=390, y=870) → "5 Amended Return? – No" checkbox
            'Text21': chk(not self.is_amended),
            # Item 6 "Short Period Return?" occupies the RIGHT half (x≈477–533):
            # Text20 (x=477, y=870) → "6 Short Period Return? – Yes" checkbox
            'Text20': chk(self.is_short_period),
            # Text17 (x=533, y=871) → "6 Short Period Return? – No" checkbox
            'Text17': chk(not self.is_short_period),

            # ── Part I – Background Information ───────────────────────────
            # Text4  (x=234, y=840, w=241.2) → "7 Taxpayer Identification Number (TIN)"
            # Format: "NNN NNN NNN NNNNN" — spaces at sep positions align with pre-drawn "/"
            'Text4':  tin_fmt,
            # Text5  (x=549, y=839) → "8 RDO Code"
            'Text5':  '',

            # Text6  (x=25,  y=812) → "9 Taxpayer's Name / Registered Name"
            'Text6':  c.name or '',

            # Text63 (x=26,  y=784) → "10 Registered Address"
            'Text63': (
                (c.street or '')
                + (' ' + c.street2 if c.street2 else '')
            ),

            # Text8  (x=27,  y=765) → City, State portion of address
            'Text8':  (c.city or '') + (f', {c.state_id.name}' if c.state_id else ''),
            # Text19 (x=537, y=764) → "10A ZIP Code"
            'Text19': c.zip or '',

            # Text9  (x=27,  y=736) → "11 Contact Number (Landline/Cellphone)"
            'Text9':  c.phone or '',
            # Text10 (x=211, y=736) → "12 Email Address"
            'Text10': c.email or '',

            # Taxpayer Classification checkboxes (Item 13)
            # Text23 (x=162, y=716) → Micro
            'Text23': chk(self.taxpayer_classification == 'micro'),
            # Text24 (x=232, y=716) → Small
            'Text24': chk(self.taxpayer_classification == 'small'),
            # Text28 (x=302, y=716) → Medium
            'Text28': chk(self.taxpayer_classification == 'medium'),
            # Text29 (x=390, y=716) → Large
            'Text29': chk(self.taxpayer_classification == 'large'),

            # Tax Relief (Item 14)
            # Text32 (x=191, y=695) → "14 Tax Relief? – Yes" checkbox
            'Text32': chk(self.is_tax_relief),
            # Text33 (x=234, y=695) → "14 Tax Relief? – No" checkbox
            'Text33': chk(not self.is_tax_relief),
            # Text18 (x=367, y=698) → "14A If yes, specify"
            'Text18': self.tax_relief_specify or '',

            # ── Part II – Total Tax Payable ────────────────────────────────
            # Text11 (x=380, y=664) → "15 Net VAT Payable/(Excess Input Tax)"
            'Text11': amt(self.net_vat_payable),

            # Less: Tax Credits/Payments
            # Text69 (x=381, y=634) → "16 Creditable VAT Withheld"
            'Text69': amt(self.creditable_vat_withheld),
            # Text70 (x=381, y=616) → "17 Advance VAT Payments"
            'Text70': amt(self.advance_vat_payments),
            # Text71 (x=381, y=597) → "18 VAT paid in previously filed return"
            'Text71': amt(self.vat_paid_prev_return_18),
            # Text72 (x=381, y=580) → "19 Other Credits/Payment"
            'Text72': amt(self.other_credits_19),
            # Text73 (x=381, y=560) → "20 Total Tax Credits/Payment (Sum 16–19)"
            'Text73': amt(self.total_tax_credits_20),
            # Text74 (x=381, y=543) → "21 Tax Still Payable/(Excess Credits)"
            'Text74': amt(self.tax_still_payable_21),

            # Add: Penalties
            # Text75 (x=380, y=525) → "22 Surcharge"
            'Text75': amt(self.surcharge_22),
            # Text76 (x=380, y=507) → "23 Interest"
            'Text76': amt(self.interest_23),
            # Text77 (x=382, y=489) → "24 Compromise"
            'Text77': amt(self.compromise_24),
            # Text78 (x=381, y=470) → "25 Total Penalties (Sum 22–24)"
            'Text78': amt(self.total_penalties),
            # Text79 (x=381, y=451) → "26 TOTAL AMOUNT PAYABLE/(Excess Credits)"
            'Text79': amt(self.total_amount_payable),

            # ── Signature Section ──────────────────────────────────────────
            # Text25 (x=26,  y=382) → Signature area – For Individual
            'Text25': '',
            # Text26 (x=323, y=381) → Signature area – For Non-Individual
            'Text26': '',
            # Text27 (x=148, y=325) → Tax Agent Accreditation No. / Attorney's Roll No.
            'Text27': '',
            # Text80 (x=347, y=325) → Date of Issue (MM/DD/YYYY)
            'Text80': '',
            # Text81 (x=506, y=325) → Expiry Date (MM/DD/YYYY)
            'Text81': '',

            # ── Part III – Details of Payment ─────────────────────────────
            # Row 27 – Cash/Bank Debit Advice
            # Text30 (x=107, y=270) → Row 27: Drawee Bank/Agency
            'Text30': '',
            # Text83 (x=178, y=270) → Row 27: Number
            'Text83': '',
            # Text86 (x=264, y=270) → Row 27: Date (MM/DD/YYYY)
            'Text86': '',
            # Text89 (x=376, y=270) → Row 27: Amount
            'Text89': '',

            # Row 28 – Check
            # Text82 (x=107, y=254) → Row 28: Drawee Bank/Agency
            'Text82': '',
            # Text84 (x=178, y=254) → Row 28: Number
            'Text84': '',
            # Text87 (x=264, y=253) → Row 28: Date (MM/DD/YYYY)
            'Text87': '',
            # Text90 (x=377, y=254) → Row 28: Amount
            'Text90': '',

            # Row 29 – Tax Debit Memo
            # Text85 (x=178, y=235) → Row 29: Number
            'Text85': '',
            # Text88 (x=264, y=236) → Row 29: Date (MM/DD/YYYY)
            'Text88': '',
            # Text91 (x=377, y=236) → Row 29: Amount
            'Text91': '',

            # Row 30 – Others (Specify below)
            # Text97 (x=22,  y=208) → Row 30: Particulars / Description
            'Text97': '',
            # Text96 (x=107, y=208) → Row 30: Drawee Bank/Agency
            'Text96': '',
            # Text95 (x=178, y=208) → Row 30: Number
            'Text95': '',
            # Text94 (x=264, y=208) → Row 30: Date (MM/DD/YYYY)
            'Text94': '',
            # Text92 (x=378, y=208) → Row 30: Amount
            'Text92': '',

            # Machine Validation / Stamp areas
            # Text37 (x=29,  y=177) → Machine Validation/ROR Details
            'Text37': '',
            # Text38 (x=382, y=166) → Stamp of Receiving Office/AAB
            'Text38': '',

            # ==============================================================
            # PAGE 2
            # ==============================================================

            # ── Page 2 Header ──────────────────────────────────────────────
            # Text39 (x=23, y=918, w=197.4) → TIN repeated on page 2
            # Same format as Text4; Tc=1.74 set in SPACED_FIELDS
            'Text39': tin_p2,
            # Text98 (x=219, y=918) → Taxpayer's Last/Registered Name (page 2)
            'Text98': c.name or '',

            # ── Part IV – Details of VAT Computation ──────────────────────

            # Total Sales and Output Tax
            # Text41  (x=163, y=876) → "31A VATable Sales (excl. VAT)"
            'Text41':  amt(self.vatable_sales),
            # Text100 (x=376, y=876) → "31B Output Tax"
            'Text100': amt(self.output_tax_31b),

            # Text99  (x=163, y=860) → "32 Zero-Rated Sales" (A col only, no output tax)
            'Text99':  amt(self.zero_rated_sales),

            # Text102 (x=163, y=844) → "33 Exempt Sales" (A col only)
            'Text102': amt(self.exempt_sales),

            # Text101 (x=163, y=828) → "34A Total Sales (Sum 31A–33A)"
            'Text101': amt(self.total_sales_34a),
            # Text103 (x=376, y=828) → "34B Total Output Tax Due (Item 31B)"
            'Text103': amt(self.total_output_34b),

            # Text104 (x=376, y=810) → "35 Less: Output VAT on Uncollected Receivables" (B col)
            'Text104': amt(self.output_vat_uncollected_35),

            # Text105 (x=376, y=795) → "36 Add: Output VAT on Recovered Uncollected Receivables" (B col)
            'Text105': amt(self.output_vat_recovered_36),

            # Text106 (x=376, y=779) → "37 Total Adjusted Output Tax Due (34B - 35 + 36)"
            'Text106': amt(self.total_adjusted_output_tax),

            # Less: Allowable Input Tax (B col = Input Tax amount)
            # Text107 (x=376, y=752) → "38 Input Tax Carried Over from Previous Quarter"
            'Text107': amt(self.input_tax_carried_over),
            # Text108 (x=376, y=736) → "39 Input Tax Deferred on Capital Goods >P1M (Sched 1 Col E)"
            'Text108': amt(self.input_tax_deferred_39),
            # Text109 (x=376, y=720) → "40 Transitional Input Tax"
            'Text109': amt(self.transitional_input_tax),
            # Text110 (x=376, y=705) → "41 Presumptive Input Tax"
            'Text110': amt(self.presumptive_input_tax),
            # Text111 (x=376, y=689) → "42 Others (Specify)"
            'Text111': amt(self.other_input_tax_42),
            # Text112 (x=376, y=672) → "43 Total (Sum of Items 38B–42B)"
            'Text112': amt(self.total_prior_input_tax_43),

            # Current Transactions – A: Purchases / B: Input Tax
            # Text117 (x=162, y=643) → "44A Domestic Purchases"
            'Text117': amt(self.domestic_purchases_44a),
            # Text113 (x=376, y=644) → "44B Domestic Input Tax"
            'Text113': amt(self.domestic_input_tax_44b),

            # Text118 (x=162, y=626) → "45A Services Rendered by Non-Residents"
            'Text118': amt(self.nonresident_services_45a),
            # Text114 (x=376, y=626) → "45B Non-Resident Input Tax"
            'Text114': amt(self.nonresident_input_tax_45b),

            # Text119 (x=162, y=609) → "46A Importations"
            'Text119': amt(self.importations_46a),
            # Text115 (x=376, y=609) → "46B Importations Input Tax"
            'Text115': amt(self.importations_input_tax_46b),

            # Text120 (x=162, y=594) → "47A Others (Specify)"
            'Text120': amt(self.other_purchases_47a),
            # Text116 (x=376, y=594) → "47B Other Input Tax"
            'Text116': amt(self.other_input_tax_47b),

            # Text121 (x=162, y=579) → "48 Domestic Purchases with No Input Tax" (A col only)
            'Text121': amt(self.domestic_no_input_48),

            # Text122 (x=162, y=563) → "49 VAT-Exempt Importations" (A col only)
            'Text122': amt(self.vat_exempt_importations_49),

            # Text123 (x=162, y=546) → "50A Total Current Purchases (Sum 44A–49A)"
            'Text123': amt(self.total_current_purchases),
            # Text124 (x=376, y=546) → "50B Total Current Input Tax (Sum 44B–47B)"
            'Text124': amt(self.total_current_input_tax),

            # Text125 (x=376, y=528) → "51 Total Available Input Tax (43B + 50B)"
            'Text125': amt(self.total_available_input_tax),

            # Less: Adjustment/Deductions from Input Tax (B col)
            # Text126 (x=376, y=503) → "52 Input Tax on Capital Goods >P1M deferred (Sched 1 Col I)"
            'Text126': amt(self.capital_goods_deferred_52),
            # Text127 (x=376, y=485) → "53 Input Tax Attributable to VAT Exempt Sales (Sched 2)"
            'Text127': amt(self.input_tax_exempt_sales_53),
            # Text128 (x=376, y=468) → "54 VAT Refund/TCC Claimed"
            'Text128': amt(self.vat_refund_tcc_54),
            # Text129 (x=376, y=453) → "55 Input VAT on Unpaid Payables"
            'Text129': amt(self.input_vat_unpaid_55),
            # Text130 (x=376, y=437) → "56 Others (Specify)"
            'Text130': amt(self.other_deductions_56),
            # Text131 (x=376, y=422) → "57 Total Deductions from Input Tax (Sum 52B–56B)"
            'Text131': amt(self.total_deductions_57),
            # Text132 (x=376, y=406) → "58 Add: Input VAT on Settled Unpaid Payables Previously Deducted"
            'Text132': amt(self.input_vat_settled_58),
            # Text133 (x=376, y=389) → "59 Adjusted Deductions from Input Tax (57B + 58B)"
            'Text133': amt(self.adjusted_deductions_59),
            # Text134 (x=376, y=373) → "60 Total Allowable Input Tax (51B Less 59B)"
            'Text134': amt(self.total_allowable_input_tax),
            # Text135 (x=376, y=356) → "61 Net VAT Payable/(Excess Input Tax) (37B Less 60B) → Part II Item 15"
            'Text135': amt(self.net_vat_payable),

            # ── Part V – Schedule 1: Amortized Input Tax from Capital Goods ─
            # Row 1 (y=280):
            # Text136 (x=23)  → (A) Date Purchased/Imported (MM/DD/YYYY)
            'Text136': '',
            # Text138 (x=79)  → (B) Source Code (D=Domestic, I=Importation)
            'Text138': '',
            # Text140 (x=112) → (C) Description
            'Text140': '',
            # Text142 (x=187) → (D) Amount of Purchases/Importation of Capital Goods >P1M
            'Text142': '',
            # Text144 (x=269) → (E) Balance of Input Tax from Previous Period
            'Text144': '',
            # Text147 (x=337) → (F) Estimated Life (in months)
            'Text147': '',
            # Text149 (x=391) → (G) Recognized Life (in Months) / Remaining Life
            'Text149': '',
            # Text151 (x=449) → (H) Allowable Input Tax for the Period (E÷G × months used)
            'Text151': '',
            # Text153 (x=518) → (I) Balance of Input Tax to be Carried to Next Period (E Less H)
            'Text153': '',

            # Row 2 (y=272):
            # Text137 (x=23)  → (A) Date
            'Text137': '',
            # Text139 (x=79)  → (B) Source Code
            'Text139': '',
            # Text141 (x=112) → (C) Description
            'Text141': '',
            # Text143 (x=187) → (D) Amount
            'Text143': '',
            # Text145 (x=269) → (E) Balance of Input Tax
            'Text145': '',
            # Text148 (x=337) → (F) Estimated Life
            'Text148': '',
            # Text150 (x=391) → (G) Recognized Life
            'Text150': '',
            # Text152 (x=449) → (H) Allowable Input Tax
            'Text152': '',
            # Text154 (x=519) → (I) Balance to Carry Forward
            'Text154': '',

            # Total row:
            # Text146 (x=269, y=264) → Total Column E → feeds Part IV Item 39B
            'Text146': '',
            # Text155 (x=518, y=263) → Total Column I → feeds Part IV Item 52B
            'Text155': '',

            # ── Part V – Schedule 2: Input Tax Attributable to VAT Exempt Sales ─
            # Text51  (x=449, y=234) → Input Tax directly attributable to VAT Exempt Sale
            'Text51':  '',
            # Text157 (x=449, y=225) → Ratable portion calculation result
            'Text157': '',
            # Text156 (x=449, y=196) → Total Input Tax attributable to Exempt Sale → Part IV Item 53
            'Text156': '',

            # ── Part V – Schedule 3: Creditable VAT Withheld ──────────────
            # Row 1 (y=165):
            # Text158 (x=23)  → (A) Period Covered
            'Text158': '',
            # Text159 (x=108) → (B) Name of Withholding Agent
            'Text159': '',
            # Text162 (x=390) → (C) Income Payment
            'Text162': '',
            # Text164 (x=503) → (D) Total Tax Withheld
            'Text164': '',

            # Row 2 (y=156):
            # Text160 (x=23)  → (A) Period Covered
            'Text160': '',
            # Text161 (x=108) → (B) Name of Withholding Agent
            'Text161': '',
            # Text163 (x=390) → (C) Income Payment
            'Text163': '',
            # Text174 (x=502) → (D) Total Tax Withheld
            'Text174': '',

            # Row 3 (y=147):
            # Text173 (x=390) → (C) Income Payment
            'Text173': '',
            # Text175 (x=502) → (D) Total Tax Withheld
            'Text175': '',

            # Text165 (x=390, y=118) → Total Column D → feeds Part II Item 16
            'Text165': amt(self.creditable_vat_withheld),

            # ── Part V – Schedule 4: Advance VAT Payment ──────────────────
            # Row 1 (y=118):
            # Text171 (x=23)  → (A) Period Covered
            'Text171': '',
            # Text169 (x=108) → (B) Name of Miller
            'Text169': '',
            # Text167 (x=255) → (C) Name of Taxpayer
            'Text167': '',
            # Text176 (x=503) → (E) Amount Paid
            'Text176': '',

            # Row 2 (y=108):
            # Text172 (x=23)  → (A) Period Covered
            'Text172': '',
            # Text170 (x=108) → (B) Name of Miller
            'Text170': '',
            # Text168 (x=255) → (C) Name of Taxpayer
            'Text168': '',
            # Text166 (x=390) → (D) Official Receipt Number
            'Text166': '',
            # Text177 (x=503) → (E) Amount Paid row 2
            'Text177': '',

            # Text178 (x=503, y=99) → Total Amount → feeds Part II Item 17
            'Text178': amt(self.advance_vat_payments),
        }
        return field_map

    # ====================================================================
    # Generate & download filled PDF
    # ====================================================================
    def action_generate_pdf(self):
        self.ensure_one()
        from pypdf import PdfReader, PdfWriter
        from pypdf.generic import NameObject, DictionaryObject, NumberObject, create_string_object

        module_path   = os.path.dirname(os.path.abspath(__file__))
        template_path = os.path.normpath(
            os.path.join(module_path, '..', 'static','src' , 'pdf', '2550Q.pdf')
        )
        if not os.path.exists(template_path):
            raise UserError(_(
                'BIR Form 2550Q PDF template not found.\n\nExpected at:\n%s'
            ) % template_path)

        field_map = self._build_pdf_field_map()

        reader = PdfReader(template_path)
        writer = PdfWriter()
        writer.append(reader)

        # Fields with custom letter-spacing (Tc) so each character lands
        # precisely in its individual PDF box.
        # Formula: Tc = field_width / num_input_chars - font_size(9.2307692)
        #
        # TIN (Text4/Text39): 17 chars "NNN NNN NNN NNNNN"
        #   Text4  w=241.2/17=14.19 → Tc=4.96
        #   Text39 w=197.4/17=11.61 → Tc=2.38
        # Year Ended (Text1): 6 chars "MMYYYY" (slash pre-drawn)
        #   w=85.4/6=14.23 → Tc=5.00
        # Date From (Text2): 8 chars "MMDDYYYY" (slashes pre-drawn)
        #   w=112.9/8=14.11 → Tc=4.88
        # Date To (Text3): 8 chars "MMDDYYYY" (slashes pre-drawn)
        #   w=113.9/8=14.24 → Tc=5.01
        SPACED_FIELDS = {
            # TIN — spaces sit over pre-drawn "/" separators (17 cells)
            'Text4':  ('10 Tc', 9.2),   # TIN page 1  (w=241.2 / 17)
            'Text39': ('8.5 Tc', 9.2),   # TIN page 2  (w=197.4 / 17)
            # Year Ended — 6 digits "MMYYYY", slash is pre-drawn (6 cells)
            'Text1':  ('5.00 Tc', 9.2),   # Year Ended  (w=85.4  /  6)
            # Dates — 8 digits "MMDDYYYY", slashes are pre-drawn (8 cells each)
            'Text2':  ('10 Tc', 9.2),   # Period From (w=112.9 /  8)
            'Text3':  ('10 Tc', 9.2),   # Period To   (w=113.9 /  8)
            # Name / address fields — moderate spacing for letter cells
            'Text6':  ('5 Tc',   9.2),    # Taxpayer Name p1
            'Text98': ('5 Tc',   9.2),    # Taxpayer Name p2
            'Text63': ('5 Tc',   9.2),    # Registered Address
            'Text8':  ('5 Tc',   9.2),    # City/State
            'Text9':  ('5 Tc',   9.2),    # Contact Number
            'Text10': ('5 Tc',   9.2),    # Email
        }

        for page in writer.pages:
            if '/Annots' not in page:
                continue
            for annot in page['/Annots']:
                obj = annot.get_object()
                if obj.get('/Subtype') != '/Widget':
                    continue
                name = str(obj.get('/T', ''))
                if name in SPACED_FIELDS:
                    tc, fs = SPACED_FIELDS[name]
                    # PDF DA syntax: color → char-spacing → font
                    # Tc must be set BEFORE Tf so it applies when text is rendered
                    obj[NameObject('/DA')] = create_string_object(
                        f'.2666667 .2666667 .2666667 rg\n{tc}\n/F0 {fs} Tf'
                    )
                # Remove visible field borders so filled values blend cleanly
                obj[NameObject('/BS')] = DictionaryObject({
                    NameObject('/W'): NumberObject(0)
                })
                if '/MK' in obj:
                    mk = obj['/MK'].get_object()
                    new_mk = DictionaryObject({
                        NameObject(k): v
                        for k, v in mk.items()
                        if k not in ('/BC', '/BG')
                    })
                    obj[NameObject('/MK')] = new_mk

        for page in writer.pages:
            writer.update_page_form_field_values(page, field_map)

        buf = io.BytesIO()
        writer.write(buf)

        safe_year = (self.year_ended or '').replace('/', '_')
        filename  = f'BIR_2550Q_{safe_year}_{self.quarter}Q_{self.company_id.name}.pdf'
        attachment = self.env['ir.attachment'].create({
            'name':      filename,
            'type':      'binary',
            'datas':     base64.b64encode(buf.getvalue()),
            'res_model': self._name,
            'res_id':    self.id,
            'mimetype':  'application/pdf',
        })
        return {
            'type':   'ir.actions.act_url',
            'url':    f'/web/content/{attachment.id}?download=true',
            'target': 'self',
        }