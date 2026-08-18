from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
from datetime import datetime, date
from dateutil.relativedelta import relativedelta
import base64
import calendar
import os                                                    # ← add this
from io import BytesIO
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject, DictionaryObject, NumberObject, create_string_object


class Bir1601EQ(models.Model):
    _name = "bir.1601eq"
    _description = "BIR 1601-EQ Quarterly Return"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "year desc, quarter desc"
    _rec_name = "name"

    # ========================================================
    # BASIC INFO
    # ========================================================
    name = fields.Char(
        string="Reference No.",
        required=True,
        copy=False,
        readonly=True,
        default="New",
    )

    state = fields.Selection([
        ('draft', 'Draft'),
        ('confirmed', 'Confirmed'),
        ('filed', 'Filed'),
        ('cancelled', 'Cancelled'),
    ], default='draft', tracking=True)

    company_id = fields.Many2one(
        'res.company',
        required=True,
        default=lambda self: self.env.company,
        tracking=True
    )

    currency_id = fields.Many2one(
        'res.currency',
        related="company_id.currency_id",
        store=True
    )

    # ========================================================
    # PART I – BACKGROUND INFORMATION
    # ========================================================
    year = fields.Integer(required=True, tracking=True)

    quarter = fields.Selection([
        ('1', '1st Quarter'),
        ('2', '2nd Quarter'),
        ('3', '3rd Quarter'),
        ('4', '4th Quarter'),
    ], required=True, tracking=True)

    return_period_from = fields.Date(
        compute="_compute_return_period",
        store=True
    )

    return_period_to = fields.Date(
        compute="_compute_return_period",
        store=True
    )

    is_amended = fields.Boolean(string="Amended Return")
    any_taxes_withheld = fields.Boolean(string="Any Taxes Withheld?")
    no_of_sheets = fields.Integer(string="No. of Sheets Attached")

    tin = fields.Char(string="TIN")
    rdo_code = fields.Char(string="RDO Code")
    withholding_agent_name = fields.Char(string="Withholding Agent Name")

    category = fields.Selection([
        ('private', 'Private'),
        ('government', 'Government'),
    ], string="Category of Withholding Agent")

    contact_number = fields.Char()
    email = fields.Char()
    zip_code = fields.Char()

    registered_address = fields.Text(
        compute="_compute_registered_address",
        store=True
    )

    # ========================================================
    # PART II – TAX COMPUTATION
    # ========================================================
    line_ids = fields.One2many(
        "bir.1601eq.line",
        "parent_id",
        string="ATC Lines"
    )

    total_tax_withheld = fields.Monetary(
        compute="_compute_totals",
        store=True
    )

    # Remittances
    remittance_1st_month = fields.Monetary()
    remittance_2nd_month = fields.Monetary()
    tax_remitted_prev = fields.Monetary()
    over_remittance_prev = fields.Monetary()
    other_payments = fields.Monetary()

    total_remittances = fields.Monetary(
        compute="_compute_totals",
        store=True
    )

    tax_still_due = fields.Monetary(
        compute="_compute_totals",
        store=True
    )

    # Penalties
    surcharge = fields.Monetary()
    interest = fields.Monetary()
    compromise = fields.Monetary()

    total_penalties = fields.Monetary(
        compute="_compute_totals",
        store=True
    )

    total_amount_due = fields.Monetary(
        compute="_compute_totals",
        store=True
    )

    over_remittance_option = fields.Selection([
        ('refund', 'To be Refunded'),
        ('tcc', 'To be Issued Tax Credit Certificate'),
        ('carry_over', 'To be Carried Over to Next Quarter'),
    ])

    # ========================================================
    # PART III – PAYMENT DETAILS
    # ========================================================
    payment_cash = fields.Monetary(string="Cash/Bank Debit Memo")
    payment_check = fields.Monetary(string="Check")
    payment_tax_debit = fields.Monetary(string="Tax Debit Memo")
    payment_others = fields.Monetary(string="Others")
    other_payment_details = fields.Text()

    # ========================================================
    # CREATE OVERRIDE FOR SEQUENCE
    # ========================================================
    @api.model
    def create(self, vals):
        if vals.get('name', 'New') == 'New':
            sequence = self.env['ir.sequence'].next_by_code('bir.1601eq')
            vals['name'] = sequence or 'New'
        return super().create(vals)

    # ========================================================
    # AUTOFILL COMPANY DETAILS
    # ========================================================
    @api.onchange('company_id')
    def _onchange_company_id(self):
        """Autofill TIN and other company details"""
        if self.company_id:
            self.tin = self.company_id.vat or ''
            self.withholding_agent_name = self.company_id.name or ''
            self.contact_number = self.company_id.phone or ''
            self.email = self.company_id.email or ''
            self.zip_code = self.company_id.zip or ''

    # ========================================================
    # COMPUTE REGISTERED ADDRESS
    # ========================================================
    @api.depends(
        'company_id.street',
        'company_id.street2',
        'company_id.city',
        'company_id.state_id',
        'company_id.country_id'
    )
    def _compute_registered_address(self):
        for rec in self:
            company = rec.company_id
            parts = [
                company.street or '',
                company.street2 or '',
                company.city or '',
                company.state_id.name if company.state_id else '',
                company.country_id.name if company.country_id else '',
            ]
            rec.registered_address = ', '.join(filter(None, parts))

    # ========================================================
    # COMPUTE TOTALS
    # ========================================================
    @api.depends(
        "line_ids.tax_withheld",
        "remittance_1st_month",
        "remittance_2nd_month",
        "tax_remitted_prev",
        "over_remittance_prev",
        "other_payments",
        "surcharge",
        "interest",
        "compromise",
    )
    def _compute_totals(self):
        for rec in self:
            rec.total_tax_withheld = sum(rec.line_ids.mapped("tax_withheld"))

            rec.total_remittances = sum([
                rec.remittance_1st_month or 0,
                rec.remittance_2nd_month or 0,
                rec.tax_remitted_prev or 0,
                rec.over_remittance_prev or 0,
                rec.other_payments or 0,
            ])

            rec.tax_still_due = rec.total_tax_withheld - rec.total_remittances

            rec.total_penalties = sum([
                rec.surcharge or 0,
                rec.interest or 0,
                rec.compromise or 0,
            ])

            rec.total_amount_due = rec.tax_still_due + rec.total_penalties

    # ========================================================
    # AUTO QUARTER DATE RANGE
    # ========================================================
    @api.depends('year', 'quarter')
    def _compute_return_period(self):
        for rec in self:
            if rec.year and rec.quarter:
                q = int(rec.quarter)
                start_month = (q - 1) * 3 + 1
                end_month = start_month + 2
                last_day = calendar.monthrange(rec.year, end_month)[1]

                rec.return_period_from = date(rec.year, start_month, 1)
                rec.return_period_to = date(rec.year, end_month, last_day)
            else:
                rec.return_period_from = False
                rec.return_period_to = False

    def _inverse_return_period_from(self):
        pass

    def _inverse_return_period_to(self):
        pass

    # ========================================================
    # WORKFLOW BUTTONS
    # ========================================================
    def action_generate(self):
        """Generate withholding tax lines using SQL (fast & accurate)"""
        self.ensure_one()

        if self.state != 'draft':
            raise ValidationError(_("You can only generate in Draft state."))

        if not self.return_period_from or not self.return_period_to:
            raise ValidationError(_("Please set Year and Quarter first."))

        # Clear existing lines
        self.write({'line_ids': [(5, 0, 0)]})

        query = """
            SELECT
                COALESCE(t.name->>'en_US', 'Unspecified') AS atc,
                SUM(ABS(aml.tax_base_amount)) AS tax_base,
                MAX(COALESCE(t.amount, 0.0)) AS tax_rate,
                SUM(ABS(aml.balance)) AS tax_withheld
            FROM account_move_line aml
            JOIN account_move am ON am.id = aml.move_id
            LEFT JOIN account_tax t ON t.id = aml.tax_line_id
            WHERE am.company_id = %s
            AND am.date >= %s
            AND am.date <= %s
            AND am.state = 'posted'
            AND LOWER(COALESCE(aml.name, '')) LIKE '%%withholding%%'
            GROUP BY t.name
        """

        self.env.cr.execute(query, (
            self.company_id.id,
            self.return_period_from,
            self.return_period_to,
        ))

        results = self.env.cr.dictfetchall()

        # Build Odoo lines
        lines_vals = []
        for row in results:
            lines_vals.append((0, 0, {
                'atc': row['atc'],
                'tax_base': row['tax_base'] or 0.0,
                'tax_rate': row['tax_rate'] or 0.0,
                'tax_withheld': row['tax_withheld'] or 0.0,
            }))

        if lines_vals:
            self.write({'line_ids': lines_vals})

        self._compute_totals()

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Success'),
                'message': _('%s withholding tax line(s) generated.') % len(lines_vals),
                'type': 'success',
                'sticky': False,
            },
            'target': 'self',
            'next': {'type': 'ir.actions.client', 'tag': 'reload'},
        }

    def action_confirm(self):
        self.state = 'confirmed'

    def action_file(self):
        self.state = 'filed'

    def action_cancel(self):
        self.state = 'cancelled'

    def action_set_draft(self):
        self.state = 'draft'

    # ========================================================
    # PDF HELPERS
    # ========================================================
    def _fmt_date(self, d):
        """Format date as MM/DD/YYYY."""
        if not d:
            return ''
        if isinstance(d, str):
            try:
                d = datetime.strptime(d, '%Y-%m-%d').date()
            except ValueError:
                return d
        return d.strftime('%m/%d/%Y')

    def _fmt_amt(self, value):
        """Format float as comma-separated number string."""
        if not value:
            return ''
        return f'{value:,.2f}'


    def _fmt_atc(self, value, max_width=12):
        """
        Format ATC code for Text21-26 with proper character spacing.
        Each character gets a space between them, plus a space on the right side.
        
        """
        if not value:
            return ''
        
        # Convert to string and strip
        atc = str(value).strip().upper()
        
        # Add space between each character
        spaced = '    '.join(atc)
        
        # Add space sa PINAKA-RIGHT SIDE (after last character)
        spaced = spaced + ' '
        
        # Right-align with leading spaces
        if len(spaced) < max_width:
            return ' ' * (max_width - len(spaced)) + spaced
        
        return spaced


    def _fmt_box_tax_base(self, value, max_width=18):
        """
        Format tax base amount for Text27-32 only.
        Iba ang spacing - 2 spaces lang between characters.
        """
        if not value or value == 0:
            return ''
        
        amt_str = f'{abs(value):,.2f}'
        digits = amt_str.replace(',', '')
        
        char_count = len(digits)
        if value < 0:
            char_count += 1
        
        spaces_between = char_count - 1
        total_len = char_count + spaces_between
        
        if value < 0:
            total_len += 1
        
        if total_len < max_width:
            leading_spaces = max_width - total_len
        else:
            leading_spaces = 0
        
        char_list = list(digits)
        spaced_parts = []
        total_digits = len(char_list)
        
        adjust_from_right = [2, 5, 8, 11]  # 5th at 8th digit from right
        
        for i, char in enumerate(char_list):
            pos_from_right = total_digits - 1 - i
            
            if i == 0:
                spaced_parts.append(str(char))
            elif pos_from_right in adjust_from_right:
                spaced_parts.append(' ' + str(char))  # 1 space lang (bawas ng 1)
            else:
                spaced_parts.append('  ' + str(char))  # 2 spaces (normal)
        
        spaced = '  '.join(spaced_parts)
        
        if value < 0:
            spaced = f'- {spaced}'
        
        result = '  ' * leading_spaces + spaced + ' '
        
        return result


    def _fmt_box_amt(self, value, max_width=18):
        """
        Format amount for normal fields (Text45-57, payment fields, etc.)
        """
        if not value or value == 0:
            return ''
        
        amt_str = f'{abs(value):,.2f}'
        digits = amt_str.replace(',', '')
        
        char_count = len(digits)
        if value < 0:
            char_count += 1
        
        spaces_between = char_count - 1
        total_len = char_count + spaces_between
        
        if value < 0:
            total_len += 1
        
        if total_len < max_width:
            leading_spaces = max_width - total_len
        else:
            leading_spaces = 0
        
        char_list = list(digits)
        spaced_parts = []
        total_digits = len(char_list)
        
        # Normal amount spacing: 4 spaces between characters
        adjust_from_right = [2, 5, 8, 11]  # 9th and 12th from right
        
        for i, char in enumerate(char_list):
            pos_from_right = total_digits - 1 - i
            
            if i == 0:
                spaced_parts.append(char)
            elif pos_from_right in adjust_from_right:
                spaced_parts.append('   ' + char)  # 3 spaces (bawas)
            else:
                spaced_parts.append('    ' + char)  # 4 spaces (normal)
        
        spaced = ''.join(spaced_parts)
        
        if value < 0:
            spaced = f'- {spaced}'
        
        result = '  ' * leading_spaces + spaced + '  '
        
        return result

    def _fmt_box_payment(self, value, max_width=18, char_spacing=3, right_padding=2):
        """
        Format payment amount fields (Text74-77).
        May adjustment sa specific digits from right.
        """
        if not value or value == 0:
            return ''
        
        amt_str = f'{abs(value):,.2f}'
        digits = amt_str.replace(',', '')
        
        char_list = list(digits)
        spaced_parts = []
        total_digits = len(char_list)
        
        # IKAW ANG BAHALA KUNG ANUNG DIGITS ANG BABAWASAN NG SPACE
        # Halimbawa: bawas sa 3rd, 6th, 9th, 12th from right
        adjust_from_right = [2, 8]
        
        for i, char in enumerate(char_list):
            pos_from_right = total_digits - 1 - i
            
            if i == 0:
                spaced_parts.append(str(char))
            elif pos_from_right in adjust_from_right:
                # Bawasan ng 1 space (e.g., 2 spaces instead of 3)
                spaced_parts.append(' ' * (char_spacing - 1) + str(char))
            else:
                spaced_parts.append(' ' * char_spacing + str(char))
        
        spaced = ' '.join(spaced_parts)
        
        if value < 0:
            spaced = f'- {spaced}'
        
        # Add right padding
        spaced = spaced + (' ' * right_padding)
        
        # Right-align
        if len(spaced) < max_width:
            return ' ' * (max_width - len(spaced)) + spaced
        
        return spaced

    def _format_tin_for_pdf(self, tin_raw):
        """
        Format TIN for the 1601-EQ PDF field.
        The PDF has printed '/' separators at fixed positions.
        We insert spaces at those positions so digits land in the correct boxes.
        TIN format: NNN-NNN-NNN-NNNNN → 'NNN NNN NNN NNNNN' (spaces skip the printed slashes)
        """
        digits = (tin_raw or '').replace('-', '').replace(' ', '')
        seg1 = digits[0:3]    # 3 digits before first /
        seg2 = digits[3:6]    # 3 digits before second /
        seg3 = digits[6:9]    # 3 digits before third /
        seg4 = digits[9:]     # branch code (up to 5 digits)
        return f'{seg1} {seg2} {seg3} {seg4}'

    # ========================================================
    # PDF FIELD MAP
    # ========================================================
    def _build_pdf_field_map(self):
        """
        Maps every Odoo field to its corresponding PDF form field.

        HOW TO LOCATE A FIELD IN THE PDF:
        Each entry shows: 'PDFFieldName' → form item label + page/position hint
        Position hint format: P1 y=### x=### (Page, vertical pos from bottom, horizontal pos from left)
        Higher y = higher on the page. The PDF page height is ~936 pts.

        To edit a specific mapping, find the field by its item number or label below.
        """
        self.ensure_one()

        # ── ATC Lines: 6 rows in the PDF (Items 13–18) ───────────────────
        # Each row: (ATC field, Tax Base field, Tax Rate field, Tax Withheld field)
        # PDF only shows 6 rows — full list must be e-submitted separately per BIR rules
        ATC_ROWS = [
            # PDF Field  | Form Label        | Page/Position
            ('Text21', 'Text27', 'Text33', 'Text39'),  # Item 13 | P1 y=641
            ('Text22', 'Text28', 'Text34', 'Text40'),  # Item 14 | P1 y=623
            ('Text23', 'Text29', 'Text35', 'Text41'),  # Item 15 | P1 y=606
            ('Text24', 'Text30', 'Text36', 'Text42'),  # Item 16 | P1 y=589
            ('Text25', 'Text31', 'Text37', 'Text43'),  # Item 17 | P1 y=570
            ('Text26', 'Text32', 'Text38', 'Text44'),  # Item 18 | P1 y=554
        ]

        field_map = {

            # ==============================================================
            # PAGE 1 – HEADER
            # ==============================================================

            # Item 1 – For the Year
            # P1 | y=827 | x=30
            'Text1': str(self.year) if self.year else '',

            # Item 2 – Quarter checkboxes (enter 'X' to mark)
            # P1 | y=823 | x=118  → 1st Quarter
            'Text2': 'X' if self.quarter == '1' else '',
            # P1 | y=822 | x=162  → 2nd Quarter
            'Text3': 'X' if self.quarter == '2' else '',
            # P1 | y=822 | x=204  → 3rd Quarter
            'Text4': 'X' if self.quarter == '3' else '',
            # P1 | y=823 | x=248  → 4th Quarter
            'Text5': 'X' if self.quarter == '4' else '',

            # Item 3 – Amended Return? (enter 'X' to mark)
            # P1 | y=822 | x=306  → Yes
            'Text6': 'X' if self.is_amended else '',
            # P1 | y=822 | x=349  → No
            'Text7': '' if self.is_amended else 'X',

            # Item 4 – Any Taxes Withheld? (enter 'X' to mark)
            # P1 | y=822 | x=407  → Yes
            'Text8': 'X' if self.any_taxes_withheld else '',
            # P1 | y=822 | x=450  → No
            'Text9': '' if self.any_taxes_withheld else 'X',

            # Item 5 – No. of Sheet/s Attached
            # P1 | y=827 | x=522
            'Text10': str(self.no_of_sheets) if self.no_of_sheets else '',

            # ==============================================================
            # PAGE 1 – PART I: BACKGROUND INFORMATION
            # ==============================================================

            # Item 6 – Taxpayer Identification Number (TIN)
            # P1 | y=793 | x=233
            
            'Text11': self._format_tin_for_pdf(self.tin),

            # Item 7 – RDO Code
            # P1 | y=792 | x=548
            'Text12': self.rdo_code or '',

            # Item 8 – Withholding Agent's Name
            # P1 | y=763 | x=18
            'Text13': self.withholding_agent_name or '',

            # Item 9 – Registered Address (line 1: street)
            # P1 | y=737 | x=18
            'Text14': self.registered_address or '',

            # Item 9 – Registered Address (line 2: city/state)
            # P1 | y=719 | x=18
            'Text15': (self.company_id.city or '') + (
                f', {self.company_id.state_id.name}' if self.company_id.state_id else ''
            ),

            # Item 9A – ZIP Code
            # P1 | y=719 | x=533
            'Text16': self.zip_code or '',

            # Item 10 – Contact Number
            # P1 | y=702 | x=104
            'Text17': self.contact_number or '',

            # Item 11 – Category of Withholding Agent: Private (enter 'X')
            # P1 | y=699 | x=446
            'Text19': 'X' if self.category == 'private' else '',

            # Item 11 – Category of Withholding Agent: Government (enter 'X')
            # P1 | y=698 | x=520
            'Text20': 'X' if self.category == 'government' else '',

            # Item 12 – Email Address
            # P1 | y=685 | x=104
            'Text18': self.email or '',

            # ==============================================================
            # PAGE 1 – PART II: COMPUTATION OF TAX
            # ==============================================================

            # Item 19 – Total Taxes Withheld for the Quarter (Sum of Items 13–18)
            # P1 | y=536 | x=392
            'Text45': self._fmt_box_amt(self.total_tax_withheld),

            # Item 20 – Less: Remittances Made – 1st Month of the Quarter
            # P1 | y=518 | x=391
            'Text46': self._fmt_box_amt(self.remittance_1st_month),

            # Item 21 – 2nd Month of the Quarter
            # P1 | y=501 | x=392
            'Text47': self._fmt_box_amt(self.remittance_2nd_month),

            # Item 22 – Tax Remitted in Return Previously Filed (amended return)
            # P1 | y=484 | x=390
            'Text48': self._fmt_box_amt(self.tax_remitted_prev),

            # Item 23 – Over-remittance from Previous Quarter
            # P1 | y=466 | x=392
            'Text49': self._fmt_box_amt(self.over_remittance_prev),

            # Item 24 – Other Payments Made
            # P1 | y=449 | x=390
            'Text50': self._fmt_box_amt(self.other_payments),

            # Item 25 – Total Remittances Made (Sum of Items 20–24)
            # P1 | y=431 | x=390
            'Text51': self._fmt_box_amt(self.total_remittances),

            # Item 26 – Tax Still Due/(Over-remittance) (Item 19 Less Item 25)
            # P1 | y=414 | x=392
            'Text52': self._fmt_box_amt(self.tax_still_due),

            # Item 27 – Surcharge
            # P1 | y=396 | x=392
            'Text53': self._fmt_box_amt(self.surcharge),

            # Item 28 – Interest
            # P1 | y=380 | x=392
            'Text54': self._fmt_box_amt(self.interest),

            # Item 29 – Compromise
            # P1 | y=361 | x=391
            'Text55': self._fmt_box_amt(self.compromise),

            # Item 30 – Total Penalties (Sum of Items 27–29)
            # P1 | y=345 | x=392
            'Text56': self._fmt_box_amt(self.total_penalties),

            # Item 31 – TOTAL AMOUNT STILL DUE/(Over-remittance)
            # P1 | y=328 | x=392
            'Text57': self._fmt_box_amt(self.total_amount_due),
            
            # Over-remittance options (enter 'X' to mark one)
            # P1 | y=305 | x=192  → To be Refunded
            'Text58': 'X' if self.over_remittance_option == 'refund' else '',
            # P1 | y=305 | x=267  → To be Issued Tax Credit Certificate
            'Text59': 'X' if self.over_remittance_option == 'tcc' else '',
            # P1 | y=305 | x=413  → To be Carried Over to Next Quarter
            'Text60': 'X' if self.over_remittance_option == 'carry_over' else '',

            # ==============================================================
            # PAGE 1 – SIGNATURE SECTION
            # ==============================================================

            # Signature over Printed Name – For Individual
            # P1 | y=257 | x=22
            'Text61': '',   # Add signatory_name field to model if needed

            # Signature over Printed Name – For Non-Individual
            # P1 | y=257 | x=311
            'Text62': '',   # Add signatory_title field to model if needed

            # Tax Agent Accreditation No. / Attorney's Roll No.
            # P1 | y=210 | x=132
            'Text63': '',   # Add tax_agent_accred field to model if needed

            # Date of Issue (MM/DD/YYYY)
            # P1 | y=210 | x=348
            'Text64': '',   # Add accred_issue_date field to model if needed

            # Date of Expiry (MM/DD/YYYY)
            # P1 | y=210 | x=505
            'Text65': '',   # Add accred_expiry_date field to model if needed

            # ==============================================================
            # PAGE 1 – PART III: DETAILS OF PAYMENT
            # ==============================================================

            # Item 32 – Cash/Bank Debit Memo
            # P1 | y=164 | x=118  → Drawee Bank/Agency
            'Text66': '',
            # P1 | y=164 | x=190  → Number
            'Text68': '',
            # P1 | y=163 | x=276  → Date
            'Text69': '',
            # P1 | y=163 | x=390  → Amount
            'Text74': self._fmt_box_payment(self.payment_cash),

            # Item 33 – Check
            # P1 | y=146 | x=118  → Drawee Bank/Agency
            'Text67': '',
            # P1 | y=146 | x=191  → Number
            'Text71': '',
            # P1 | y=146 | x=277  → Date
            'Text72': '',
            # P1 | y=146 | x=390  → Amount
            'Text75': self._fmt_box_payment(self.payment_check),

            # Item 34 – Tax Debit Memo
            # P1 | y=128 | x=190  → Number
            'Text70': '',
            # P1 | y=128 | x=276  → Date
            'Text73': '',
            # P1 | y=128 | x=390  → Amount
            'Text76': self._fmt_box_payment(self.payment_tax_debit),

            # Item 35 – Others (specify below)
            # P1 | y=100 | x=18   → Specify text
            'Text81': self.other_payment_details or '',
            # P1 | y=100 | x=119  → Drawee Bank/Agency
            'Text80': '',
            # P1 | y=100 | x=190  → Number
            'Text79': '',
            # P1 | y=100 | x=276  → Date
            'Text78': '',
            # P1 | y=100 | x=390  → Amount
            'Text77': self._fmt_box_payment(self.payment_others),
        }

        # ==================================================================
        # ATC LINES – Items 13 to 18 (6 rows in the PDF)
        # Columns: ATC | Tax Base | Tax Rate | Tax Withheld
        # ==================================================================
        for i, (atc_k, base_k, rate_k, tax_k) in enumerate(ATC_ROWS):
            line = self.line_ids[i] if i < len(self.line_ids) else None
            if line:
                field_map[atc_k] = self._fmt_atc(line.atc)                     # ATC code
                field_map[base_k] = self._fmt_box_tax_base(line.tax_base)       # Tax Base
                field_map[rate_k] = f'{line.tax_rate}%' if line.tax_rate else ''  # Tax Rate
                field_map[tax_k]  = self._fmt_box_amt(line.tax_withheld)   # Tax Withheld
            else:
                field_map[atc_k]  = ''
                field_map[base_k] = ''
                field_map[rate_k] = ''
                field_map[tax_k]  = ''

        return field_map


    # ========================================================
    # GENERATE PDF
    # ========================================================
    def action_generate_pdf(self):
        """
        Fill the official BIR Form 1601-EQ PDF template with Odoo data
        and return a download action.

        Place the template PDF at:
            <your_module>/static/src/pdf/1601eq.pdf
        """
        self.ensure_one()

        module_path = os.path.dirname(os.path.abspath(__file__))
        template_path = os.path.join(module_path, '..', 'static', 'src', 'pdf', '1601eq.pdf')
        template_path = os.path.normpath(template_path)

        if not os.path.exists(template_path):
            raise UserError(_(
                'BIR Form 1601-EQ PDF template not found.\n\n'
                'Please place the official BIR Form 1601-EQ PDF at:\n'
                '  %s'
            ) % template_path)

        field_map = self._build_pdf_field_map()

        reader = PdfReader(template_path)
        writer = PdfWriter()
        writer.append(reader)

        # 1. Custom character spacing definition for header fields
        SPACED_FIELDS = {
            'Text11': ('10 Tc', 9.2),   # Item 6  – TIN
            'Text10': ('9 Tc', 9.2),    # Item 5  – No. of Sheets
            'Text1':  ('9 Tc', 9.2),    # Item 1  – Year
            'Text12': ('12 Tc', 9.2),   # Item 7  – RDO Code
            'Text13': ('0 Tc', 9.2),    # Item 8  – Withholding Agent Name
            'Text14': ('0 Tc', 9.2),    # Item 9  – Registered Address line 1
            'Text15': ('0 Tc', 9.2),    # Item 9  – Registered Address line 2
            'Text17': ('3 Tc', 9.2),    # Item 10 – Contact Number
            'Text18': ('3 Tc', 9.2),    # Item 12 – Email Address
        }

        # 2. Right-aligned fields range (Text21 hanggang Text57)
        RIGHT_ALIGN_FIELDS = [f'Text{i}' for i in range(21, 58)] + ['Text74', 'Text75', 'Text76', 'Text77']


        Y_OFFSETS = {
            'Text1': -3,    # Year - ibaba ng 2 points
            'Text11': -3,   # TIN - ibaba ng 1 point
            'Text12': -3,   # RDO - ibaba ng 1 point
            'Text10': -3,   # No. of Sheets - ibaba ng 1 point
            'Text13': 0,   # Withholding Agent Name - no adjustment
            'Text14': 0,   # Address line 1 - no adjustment
            'Text15': 0,   # Address line 2 - no adjustment
            'Text17': 0,   # Contact Number - no adjustment
            'Text18': 0,   # Email - no adjustment
            **{f'Text{i}': -3 for i in range(21, 45)},   # Text21-44: -2
            **{f'Text{i}': -3 for i in range(45, 59)},   # Text45-58: -1
            **{f'Text{i}': -2 for i in [74, 75, 76, 77]}, # Payment fields: -2
        }

        # 3. Single Pass Loop: Apply /DA, Right Align, Remove Borders, and Adjust Y
        for page in writer.pages:
            if '/Annots' in page:
                for annot in page['/Annots']:
                    obj = annot.get_object()
                    if obj.get('/Subtype') == '/Widget':
                        name = str(obj.get('/T', ''))

                        # 1. Set Right Alignment (/Q 2)
                        if name in RIGHT_ALIGN_FIELDS:
                            obj[NameObject('/Q')] = NumberObject(2)

                        # 2. Set /DA: priority ang SPACED_FIELDS bago ang default 0 Tc
                        if name in SPACED_FIELDS:
                            tc, fs = SPACED_FIELDS[name]
                            new_da = f'.2666667 .2666667 .2666667 rg\n{tc}\n/F0 {fs} Tf\n'
                            obj[NameObject('/DA')] = create_string_object(new_da)
                        elif name in RIGHT_ALIGN_FIELDS:
                            new_da = '.2666667 .2666667 .2666667 rg\n0 Tc\n/F0 9.2 Tf\n'
                            obj[NameObject('/DA')] = create_string_object(new_da)

                        # 3. Adjust Y position
                        if name in Y_OFFSETS and '/Rect' in obj:
                            rect = obj['/Rect']
                            y_offset = Y_OFFSETS[name]
                            if y_offset != 0 and len(rect) >= 4:
                                # Adjust the Y position (rect[1] is bottom, rect[3] is top)
                                rect[1] = NumberObject(rect[1].get_object() + y_offset)
                                rect[3] = NumberObject(rect[3].get_object() + y_offset)

                        # 4. Remove borders and background fill colors
                        obj[NameObject('/BS')] = DictionaryObject({
                            NameObject('/W'): NumberObject(0)
                        })
                        if '/MK' in obj:
                            mk = obj['/MK'].get_object()
                            new_mk = DictionaryObject()
                            for k, v in mk.items():
                                if k not in ('/BC', '/BG'):
                                    new_mk[NameObject(k)] = v
                            obj[NameObject('/MK')] = new_mk

        # 4. Fill field values across pages
        for page in writer.pages:
            writer.update_page_form_field_values(page, field_map, auto_regenerate=False)

        # 5. Output buffer & attachment creation
        buf = BytesIO()
        writer.write(buf)
        pdf_bytes = buf.getvalue()

        filename = f'BIR_1601EQ_{self.year}_Q{self.quarter}_{self.company_id.name}.pdf'
        attachment = self.env['ir.attachment'].create({
            'name': filename,
            'type': 'binary',
            'datas': base64.b64encode(pdf_bytes),
            'res_model': self._name,
            'res_id': self.id,
            'mimetype': 'application/pdf',
        })

        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{attachment.id}?download=true',
            'target': 'self',
        }


# ============================================================
# ATC LINE MODEL
# ============================================================

class Bir1601EQLine(models.Model):
    _name = "bir.1601eq.line"
    _description = "BIR 1601EQ ATC Lines"

    parent_id = fields.Many2one(
        "bir.1601eq",
        required=True,
        ondelete="cascade"
    )

    atc = fields.Char(string="ATC")
    nature_of_payment = fields.Char()
    tax_base = fields.Monetary()
    tax_rate = fields.Float()
    tax_withheld = fields.Monetary()

    currency_id = fields.Many2one(
        related="parent_id.currency_id",
        store=True
    )