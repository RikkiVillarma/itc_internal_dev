# -*- coding: utf-8 -*-
from odoo import models, fields, api
from odoo.exceptions import ValidationError
import xlsxwriter
import base64
from io import BytesIO
from datetime import datetime, date
from dateutil.relativedelta import relativedelta


import os
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject, DictionaryObject, NumberObject, create_string_object
import pypdf


class BIR1604E(models.Model):
    _name = 'bir.1604e'
    _description = 'BIR Form 1604-E - Annual Information Return'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'year desc'

    name = fields.Char(string='Reference', required=True, copy=False, readonly=True, default='New')
    
    # Period Information
    year = fields.Integer(string='Year', required=True, default=lambda self: datetime.now().year, tracking=True)
    date_from = fields.Date(string='Period From', compute='_compute_period_dates', store=True)
    date_to = fields.Date(string='Period To', compute='_compute_period_dates', store=True)
    
    # Company Information
    company_id = fields.Many2one('res.company', string='Company', required=True, default=lambda self: self.env.company)
    tin = fields.Char(string='TIN', related='company_id.vat', readonly=True)
    withholding_agent_name = fields.Char(string='Withholding Agent Name', related='company_id.name', readonly=True)
    registered_address = fields.Text(string='Registered Address', compute='_compute_company_address', store=True)
    rdo_code = fields.Char(string='RDO Code')
    line_of_business = fields.Char(string='Line of Business/Occupation')
    
    # Return Information
    amended_return = fields.Boolean(string='Amended Return?', default=False)
    num_sheets_attached = fields.Integer(string='No. of Sheets Attached', compute='_compute_sheets_attached', store=True)
    
    # Category
    category_private = fields.Boolean(string='Private', default=True)
    category_government = fields.Boolean(string='Government', default=False)
    
    # Schedule 1: Form 1601-E Remittances
    schedule1_line_ids = fields.One2many('bir.1604e.schedule1', 'form_id', string='Schedule 1 - Form 1601-E Remittances')
    total_1601e_taxes = fields.Float(string='Total 1601-E Taxes', compute='_compute_schedule_totals', store=True)
    total_1601e_penalties = fields.Float(string='Total 1601-E Penalties', compute='_compute_schedule_totals', store=True)
    total_1601e_remitted = fields.Float(string='Total 1601-E Remitted', compute='_compute_schedule_totals', store=True)
    
    # Schedule 2: Form 1606 Remittances
    schedule2_line_ids = fields.One2many('bir.1604e.schedule2', 'form_id', string='Schedule 2 - Form 1606 Remittances')
    total_1606_taxes = fields.Float(string='Total 1606 Taxes', compute='_compute_schedule_totals', store=True)
    total_1606_penalties = fields.Float(string='Total 1606 Penalties', compute='_compute_schedule_totals', store=True)
    total_1606_remitted = fields.Float(string='Total 1606 Remitted', compute='_compute_schedule_totals', store=True)
    
    # Schedule 3: Exempt from Withholding
    schedule3_line_ids = fields.One2many('bir.1604e.schedule3', 'form_id', string='Schedule 3 - Exempt from Withholding')
    total_schedule3_amount = fields.Float(string='Total Schedule 3', compute='_compute_schedule_totals', store=True)
    
    # Schedule 4: Expanded Withholding Tax
    schedule4_line_ids = fields.One2many('bir.1604e.schedule4', 'form_id', string='Schedule 4 - Expanded Withholding Tax')
    total_schedule4_income = fields.Float(string='Total Schedule 4 Income', compute='_compute_schedule_totals', store=True)
    
    # Status
    state = fields.Selection([
        ('draft', 'Draft'),
        ('computed', 'Computed'),
        ('submitted', 'Submitted')
    ], string='Status', default='draft', tracking=True)
    
    # Signatory
    signatory_name = fields.Char(string='Signatory Name')
    signatory_title = fields.Char(string='Signatory Title')
    
    # Export
    excel_file = fields.Binary(string='Excel File', readonly=True)
    excel_filename = fields.Char(string='Excel Filename', readonly=True)
    
    report_date = fields.Date(string='Report Date', default=fields.Date.today)
    
    @api.depends('year')
    def _compute_period_dates(self):
        for record in self:
            if record.year:
                record.date_from = date(record.year, 1, 1)
                record.date_to = date(record.year, 12, 31)
    
    @api.depends('company_id')
    def _compute_company_address(self):
        for record in self:
            if record.company_id:
                address_parts = []
                if record.company_id.street:
                    address_parts.append(record.company_id.street)
                if record.company_id.street2:
                    address_parts.append(record.company_id.street2)
                if record.company_id.city:
                    address_parts.append(record.company_id.city)
                if record.company_id.state_id:
                    address_parts.append(record.company_id.state_id.name)
                if record.company_id.zip:
                    address_parts.append(record.company_id.zip)
                if record.company_id.country_id:
                    address_parts.append(record.company_id.country_id.name)
                
                record.registered_address = ', '.join(address_parts)
    
    @api.depends('schedule3_line_ids', 'schedule4_line_ids')
    def _compute_sheets_attached(self):
        for record in self:
            sheets = 0
            if record.schedule3_line_ids:
                sheets += (len(record.schedule3_line_ids) // 20) + 1
            if record.schedule4_line_ids:
                sheets += (len(record.schedule4_line_ids) // 30) + 1
            record.num_sheets_attached = sheets
    
    @api.depends('schedule1_line_ids.taxes_withheld', 'schedule1_line_ids.penalties', 'schedule1_line_ids.total_remitted',
                 'schedule2_line_ids.taxes_withheld', 'schedule2_line_ids.penalties', 'schedule2_line_ids.total_remitted',
                 'schedule3_line_ids.income_payment', 'schedule4_line_ids.income_payment')
    def _compute_schedule_totals(self):
        for record in self:
            record.total_1601e_taxes = sum(record.schedule1_line_ids.mapped('taxes_withheld'))
            record.total_1601e_penalties = sum(record.schedule1_line_ids.mapped('penalties'))
            record.total_1601e_remitted = sum(record.schedule1_line_ids.mapped('total_remitted'))
            
            record.total_1606_taxes = sum(record.schedule2_line_ids.mapped('taxes_withheld'))
            record.total_1606_penalties = sum(record.schedule2_line_ids.mapped('penalties'))
            record.total_1606_remitted = sum(record.schedule2_line_ids.mapped('total_remitted'))
            
            record.total_schedule3_amount = sum(record.schedule3_line_ids.mapped('income_payment'))
            record.total_schedule4_income = sum(record.schedule4_line_ids.mapped('income_payment'))
    
    @api.model
    def create(self, vals):
        if vals.get('name', 'New') == 'New':
            vals['name'] = self.env['ir.sequence'].next_by_code('bir.1604e') or 'New'
        return super(BIR1604E, self).create(vals)
    
    def action_generate_data(self):
        """Generate annual data from various sources"""
        self.ensure_one()
        
        # Clear existing lines
        self.schedule1_line_ids.unlink()
        self.schedule2_line_ids.unlink()
        self.schedule3_line_ids.unlink()
        self.schedule4_line_ids.unlink()
        
        # Generate Schedule 1: From quarterly 1601-EQ forms (if using that module)
        self._generate_schedule1()
        
        # Generate Schedule 2: From Form 1606 data (if exists)
        self._generate_schedule2()
        
        # Generate Schedule 3: Exempt from withholding (from vendor bills)
        self._generate_schedule3()
        
        # Generate Schedule 4: Expanded withholding tax (from vendor bills with EWT)
        self._generate_schedule4()
        
        self.state = 'computed'
        
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Success',
                'message': f'Generated annual data for year {self.year}',
                'type': 'success',
                'sticky': False,
            }
        }
    
    def _generate_schedule1(self):
        """Generate Schedule 1 from monthly/quarterly tax remittances"""
        Schedule1 = self.env['bir.1604e.schedule1']
        months_data = {}
        
        # Try to get data from BIR 1601-EQ forms if module is installed
        if 'bir.1601eq' in self.env:
            forms_1601eq = self.env['bir.1601eq'].search([
                ('year', '=', self.year),
                ('company_id', '=', self.company_id.id),
                ('state', '=', 'submitted')
            ])
            
            quarter_map = {
                '1': [(1, 'JAN'), (2, 'FEB'), (3, 'MAR')],
                '2': [(4, 'APR'), (5, 'MAY'), (6, 'JUN')],
                '3': [(7, 'JUL'), (8, 'AUG'), (9, 'SEPT')],
                '4': [(10, 'OCT'), (11, 'NOV'), (12, 'DEC')]
            }
            
            for form in forms_1601eq:
                if form.quarter in quarter_map:
                    monthly_amount = form.tax_withheld_current_month / 3
                    for month_num, month_name in quarter_map[form.quarter]:
                        months_data[month_num] = {
                            'month': month_name,
                            'taxes': monthly_amount,
                            'penalties': form.penalties / 3 if form.penalties else 0.0
                        }
        else:
            month_names = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN',
                           'JUL', 'AUG', 'SEPT', 'OCT', 'NOV', 'DEC']
            for idx, month in enumerate(month_names, 1):
                months_data[idx] = {
                    'month': month,
                    'taxes': 0.0,
                    'penalties': 0.0
                }
        
        for month_num in sorted(months_data.keys()):
            data = months_data[month_num]
            Schedule1.create({
                'form_id': self.id,
                'month': data['month'],
                'date_remittance': date(self.year, month_num, 1),
                'taxes_withheld': data['taxes'],
                'penalties': data['penalties'],
            })
    
    def _generate_schedule2(self):
        """Generate Schedule 2 from Form 1606 data"""
        Schedule2 = self.env['bir.1604e.schedule2']
        
        month_names = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN',
                       'JUL', 'AUG', 'SEPT', 'OCT', 'NOV', 'DEC']
        
        for idx, month in enumerate(month_names, 1):
            Schedule2.create({
                'form_id': self.id,
                'month': month,
                'date_remittance': date(self.year, idx, 1),
                'taxes_withheld': 0.0,
                'penalties': 0.0,
            })
    
    def _generate_schedule3(self):
        """Generate Schedule 3: Payees exempt from withholding tax"""
        Schedule3 = self.env['bir.1604e.schedule3']

        bills = self.env['account.move'].search([
            ('move_type', '=', 'in_invoice'),
            ('date', '>=', self.date_from),
            ('date', '<=', self.date_to),
            ('state', '=', 'posted'),
            ('company_id', '=', self.company_id.id),
        ])

        partner_data = {}
        for bill in bills:
            partner = bill.partner_id

            # Pick the first zero-rate purchase tax on the bill lines as the ATC reference
            atc_tax = False
            for line in bill.invoice_line_ids:
                exempt_tax = line.tax_ids.filtered(
                    lambda t: t.type_tax_use == 'purchase' and t.amount == 0
                )
                if exempt_tax:
                    atc_tax = exempt_tax[0]
                    break

            if partner.id not in partner_data:
                partner_data[partner.id] = {
                    'tin': partner.vat or '',
                    'name': partner.name,
                    'amount': 0.0,
                    'atc_id': atc_tax.id if atc_tax else False,
                }
            partner_data[partner.id]['amount'] += bill.amount_total

        seq = 1
        for partner_id, data in list(partner_data.items())[:100]:
            Schedule3.create({
                'form_id': self.id,
                'sequence': seq,
                'tin': data['tin'],
                'payee_name': data['name'],
                'atc_id': data['atc_id'],
                'nature_income': 'Professional Fees',
                'income_payment': data['amount'],
            })
            seq += 1

    def _generate_schedule4(self):
        """Generate Schedule 4: Payees subject to expanded withholding tax"""
        Schedule4 = self.env['bir.1604e.schedule4']

        bills = self.env['account.move'].search([
            ('move_type', '=', 'in_invoice'),
            ('date', '>=', self.date_from),
            ('date', '<=', self.date_to),
            ('state', '=', 'posted'),
            ('company_id', '=', self.company_id.id),
        ])

        partner_data = {}
        for bill in bills:
            partner = bill.partner_id
            wht_amount = 0.0
            base_amount = bill.amount_untaxed
            atc_tax = False

            for line in bill.invoice_line_ids:
                ewt_tax = line.tax_ids.filtered(
                    lambda t: t.type_tax_use == 'purchase' and t.amount != 0
                )
                if ewt_tax:
                    atc_tax = ewt_tax[0]

            for line in bill.line_ids:
                if 'withholding' in (line.name or '').lower() or 'ewt' in (line.name or '').lower():
                    wht_amount += abs(line.balance)

            if partner.id not in partner_data:
                partner_data[partner.id] = {
                    'tin': partner.vat or '',
                    'name': partner.name,
                    'amount': 0.0,
                    'wht': 0.0,
                    'atc_id': atc_tax.id if atc_tax else False,
                    'tax_rate': atc_tax.amount if atc_tax else 0.0,
                }
            partner_data[partner.id]['amount'] += base_amount
            partner_data[partner.id]['wht'] += wht_amount

        seq = 1
        for partner_id, data in list(partner_data.items())[:100]:
            Schedule4.create({
                'form_id': self.id,
                'sequence': seq,
                'tin': data['tin'],
                'payee_name': data['name'],
                'atc_id': data['atc_id'],
                'nature_income': 'Professional Fees',
                'income_payment': data['amount'],
                'tax_rate': data['tax_rate'],
            })
            seq += 1

    ####################
    # Generate pdf start
    ###################
    def _fmt_date(self, d):
        """Format a date value as MM/DD/YYYY for BIR forms."""
        if not d:
            return ''
        if isinstance(d, str):
            from datetime import datetime
            try:
                d = datetime.strptime(d, '%Y-%m-%d').date()
            except ValueError:
                return d
        return d.strftime('%m/%d/%Y')

    def _fmt_amt(self, value):
        """Format a float as a comma-separated number string."""
        if not value:
            return ''
        return f'{value:,.2f}'

    def _build_pdf_field_map(self):

        self.ensure_one()
        c = self.company_id
        tin_clean = (c.vat or '').replace('-', '').replace(' ', '')

        q_data = self._aggregate_quarterly_s1()
        monthly = {line.month: line for line in self.schedule2_line_ids}

        field_map = {
            # Part I
            'Text9':    str(self.year),
            'Text166':  str(self.num_sheets_attached) if self.num_sheets_attached else '',
            'Button168': '/Yes' if self.amended_return else '/Off',
            'Button169': '/Off' if self.amended_return else '/Yes',
            'Text4':    tin_clean,
            'Text167':  self.rdo_code or '',
            'Text1':    c.name or '',
            'Text2':    (c.street or '') + (' ' + (c.street2 or '') if c.street2 else ''),
            'Text3':    (c.city or '') + (f', {c.state_id.name}' if c.state_id else ''),
            'Text7':    c.zip or '',
            'Text6':    c.phone or '',
            'Text8':    c.email or '',

            # Schedule 1 – Quarterly
            # Q1
            'Text10': self._fmt_date(q_data['Q1']['date']),
            'Text11': q_data['Q1']['bank'],
            'Text14': q_data['Q1']['tra'],
            'Text19': self._fmt_amt(q_data['Q1']['tax']),
            'Text39': self._fmt_amt(q_data['Q1']['pen']),
            'Text45': self._fmt_amt(q_data['Q1']['total']),
            # Q2
            'Text32': self._fmt_date(q_data['Q2']['date']),
            'Text33': q_data['Q2']['bank'],
            'Text15': q_data['Q2']['tra'],
            'Text20': self._fmt_amt(q_data['Q2']['tax']),
            'Text17': self._fmt_amt(q_data['Q2']['pen']),
            'Text44': self._fmt_amt(q_data['Q2']['total']),
            # Q3
            'Text12': self._fmt_date(q_data['Q3']['date']),
            'Text34': q_data['Q3']['bank'],
            'Text16': q_data['Q3']['tra'],
            'Text21': self._fmt_amt(q_data['Q3']['tax']),
            'Text41': self._fmt_amt(q_data['Q3']['pen']),
            'Text43': self._fmt_amt(q_data['Q3']['total']),
            # Q4
            'Text13': self._fmt_date(q_data['Q4']['date']),
            'Text35': q_data['Q4']['bank'],
            'Text31': q_data['Q4']['tra'],
            'Text36': self._fmt_amt(q_data['Q4']['tax']),
            'Text40': self._fmt_amt(q_data['Q4']['pen']),
            'Text42': self._fmt_amt(q_data['Q4']['total']),
            # S1 Totals
            'Text37': self._fmt_amt(self.total_1601e_taxes),
            'Text38': self._fmt_amt(self.total_1601e_penalties),
            'Text18': self._fmt_amt(self.total_1601e_remitted),

            # Signature
            'Text23': f'{self.signatory_name or ""} / {self.signatory_title or ""}',
            'Text24': f'{self.signatory_name or ""} / {self.signatory_title or ""}',
            'Text25': '',
            'Text26': '',
            'Text27': '',
        }

        # Schedule 2 – Monthly
        MONTHLY_PDF_FIELDS = {
            'JAN':  ('Text22', 'Text82',  'Text69', 'Text57',  'Text95',  'Text108'),
            'FEB':  ('Text46', 'Text58',  'Text70', 'Text83',  'Text96',  'Text109'),
            'MAR':  ('Text47', 'Text59',  'Text71', 'Text84',  'Text97',  'Text110'),
            'APR':  ('Text48', 'Text60',  'Text72', 'Text85',  'Text98',  'Text111'),
            'MAY':  ('Text49', 'Text61',  'Text73', 'Text86',  'Text99',  'Text112'),
            'JUN':  ('Text50', 'Text62',  'Text74', 'Text87',  'Text100', 'Text113'),
            'JUL':  ('Text51', 'Text63',  'Text75', 'Text88',  'Text101', 'Text114'),
            'AUG':  ('Text52', 'Text64',  'Text76', 'Text89',  'Text102', 'Text115'),
            'SEPT': ('Text53', 'Text65',  'Text77', 'Text90',  'Text103', 'Text116'),
            'OCT':  ('Text54', 'Text66',  'Text78', 'Text91',  'Text104', 'Text117'),
            'NOV':  ('Text55', 'Text67',  'Text79', 'Text92',  'Text105', 'Text118'),
            'DEC':  ('Text56', 'Text68',  'Text80', 'Text93',  'Text106', 'Text119'),
        }

        total_m_tax = 0.0
        total_m_pen = 0.0
        for month_code, keys in MONTHLY_PDF_FIELDS.items():
            date_k, bank_k, tra_k, tax_k, pen_k, total_k = keys
            line = monthly.get(month_code)
            if line:
                total_m_tax += line.taxes_withheld
                total_m_pen += line.penalties
                field_map[date_k]  = self._fmt_date(line.date_remittance)
                field_map[bank_k]  = line.bank_name or ''
                field_map[tra_k]   = ''
                field_map[tax_k]   = self._fmt_amt(line.taxes_withheld)
                field_map[pen_k]   = self._fmt_amt(line.penalties)
                field_map[total_k] = self._fmt_amt(line.total_remitted)
            else:
                for k in keys:
                    field_map[k] = ''

        field_map['Text94']  = self._fmt_amt(total_m_tax)
        field_map['Text107'] = self._fmt_amt(total_m_pen)
        field_map['Text120'] = self._fmt_amt(total_m_tax + total_m_pen)

        # Page 2, Schedule 3 = schedule4_line_ids (EWT Alphalist)
        EWT_PDF_ROWS = [
            ('Text28',  'Text123', 'Text126', 'Text129', 'Text132', 'Text136', 'Text139'),
            ('Text121', 'Text124', 'Text127', 'Text130', 'Text133', 'Text137', 'Text140'),
            ('Text122', 'Text125', 'Text128', 'Text131', 'Text134', 'Text138', 'Text141'),
            (None,       None,      None,      None,      'Text135',  None,     'Text142'),
        ]
        ewt_lines = self.schedule4_line_ids
        for i, row_keys in enumerate(EWT_PDF_ROWS):
            seq_k, tin_k, name_k, atc_k, amt_k, rate_k, tax_k = row_keys
            line = ewt_lines[i] if i < len(ewt_lines) else None
            if line:
                tax_withheld = line.income_payment * (line.tax_rate / 100)
                if seq_k:   field_map[seq_k]  = str(line.sequence)
                if tin_k:   field_map[tin_k]  = line.tin or ''
                if name_k:  field_map[name_k] = line.payee_name or ''
                if atc_k:   field_map[atc_k]  = line.atc_code or ''
                if amt_k:   field_map[amt_k]  = self._fmt_amt(line.income_payment)
                if rate_k:  field_map[rate_k] = str(line.tax_rate)
                if tax_k:   field_map[tax_k]  = self._fmt_amt(tax_withheld)
            else:
                for k in row_keys:
                    if k:
                        field_map[k] = ''

        # Page 2, Schedule 4 = schedule3_line_ids (Exempt Alphalist)
        EXEMPT_PDF_ROWS = [
            ('Text29',  'Text146', 'Text150', 'Text154', 'Text158', 'Text162'),
            ('Text143', 'Text147', 'Text151', 'Text155', 'Text159', 'Text164'),
            ('Text144', 'Text148', 'Text152', 'Text156', 'Text160', 'Text163'),
            ('Text145', 'Text149', 'Text153', 'Text157', 'Text161', 'Text165'),
        ]
        exempt_lines = self.schedule3_line_ids
        for i, row_keys in enumerate(EXEMPT_PDF_ROWS):
            seq_k, tin_k, name_k, atc_k, nature_k, amt_k = row_keys
            line = exempt_lines[i] if i < len(exempt_lines) else None
            if line:
                field_map[seq_k]    = str(line.sequence)
                field_map[tin_k]    = line.tin or ''
                field_map[name_k]   = line.payee_name or ''
                field_map[atc_k]    = line.atc_code or ''
                field_map[nature_k] = line.nature_income or ''
                field_map[amt_k]    = self._fmt_amt(line.income_payment)
            else:
                for k in row_keys:
                    field_map[k] = ''

        return field_map

    def _aggregate_quarterly_s1(self):

        QUARTER_MAP = {
            'Q1': ['JAN', 'FEB', 'MAR'],
            'Q2': ['APR', 'MAY', 'JUN'],
            'Q3': ['JUL', 'AUG', 'SEPT'],
            'Q4': ['OCT', 'NOV', 'DEC'],
        }
        result = {}
        month_lookup = {line.month: line for line in self.schedule1_line_ids}

        for quarter, months in QUARTER_MAP.items():
            lines = [month_lookup[m] for m in months if m in month_lookup]
            if lines:
                last = lines[-1]
                tax  = sum(l.taxes_withheld for l in lines)
                pen  = sum(l.penalties for l in lines)
                result[quarter] = {
                    'date':  last.date_remittance,
                    'bank':  last.bank_name or '',
                    'tra':   '',
                    'tax':   tax,
                    'pen':   pen,
                    'total': tax + pen,
                }
            else:
                result[quarter] = {'date': None, 'bank': '', 'tra': '',
                                   'tax': 0.0, 'pen': 0.0, 'total': 0.0}
        return result

    def action_generate_pdf(self):

        self.ensure_one()
        import base64, io, os
        from pypdf import PdfReader, PdfWriter
        from pypdf.generic import NameObject, DictionaryObject, NumberObject, create_string_object

        module_path = os.path.dirname(os.path.abspath(__file__))
        template_path = os.path.join(module_path, '..', 'static', 'src', 'pdf', '1604E.pdf')
        template_path = os.path.normpath(template_path)

        if not os.path.exists(template_path):
            raise UserError(_(
                'BIR 1604-E PDF template not found.\n\n'
                'Please place the official BIR Form 1604-E PDF at:\n'
                '  %s'
            ) % template_path)

        field_map = self._build_pdf_field_map()

        reader = PdfReader(template_path)
        writer = PdfWriter()
        writer.append(reader)

        SPACED_FIELDS = {
            'Text4':   ('14 Tc',  9.2),
            'Text9':   ('14 Tc',  9.2),
            'Text166': ('4 Tc',   9.2),
            'Text167': ('4 Tc',   9.2),
            'Text1':   ('6 Tc',   9.2),
            'Text2':   ('6 Tc',   9.2),
            'Text3':   ('6 Tc',   9.2),
            'Text6':   ('6 Tc',   9.2),
            'Text8':   ('6 Tc',   9.2),
        }

        for page in writer.pages:
            if '/Annots' in page:
                for annot in page['/Annots']:
                    obj = annot.get_object()
                    if obj.get('/Subtype') == '/Widget':
                        name = str(obj.get('/T', ''))
                        if name in SPACED_FIELDS:
                            tc, fs = SPACED_FIELDS[name]
                            new_da = f'.2666667 .2666667 .2666667 rg\n{tc}\n/F0 {fs} Tf\n'
                            obj[NameObject('/DA')] = create_string_object(new_da)

        for page in writer.pages:
            if '/Annots' in page:
                for annot in page['/Annots']:
                    obj = annot.get_object()
                    if obj.get('/Subtype') == '/Widget':
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

        for page in writer.pages:
            writer.update_page_form_field_values(page, field_map)

        buf = io.BytesIO()
        writer.write(buf)
        pdf_bytes = buf.getvalue()

        filename = f'BIR_1604E_{self.year}_{self.company_id.name}.pdf'
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

    #####################
    # Generate pdf End
    ########################

    def action_reset_to_draft(self):
        self.state = 'draft'

    def action_submit(self):
        self.state = 'submitted'


class BIR1604ESchedule1(models.Model):
    _name = 'bir.1604e.schedule1'
    _description = 'BIR 1604-E Schedule 1 - Form 1601-E Remittances'
    _order = 'form_id, month'

    form_id = fields.Many2one('bir.1604e', string='Form', required=True, ondelete='cascade')
    month = fields.Selection([
        ('JAN', 'January'),
        ('FEB', 'February'),
        ('MAR', 'March'),
        ('APR', 'April'),
        ('MAY', 'May'),
        ('JUN', 'June'),
        ('JUL', 'July'),
        ('AUG', 'August'),
        ('SEPT', 'September'),
        ('OCT', 'October'),
        ('NOV', 'November'),
        ('DEC', 'December'),
    ], string='Month', required=True)
    date_remittance = fields.Date(string='Date of Remittance')
    bank_name = fields.Char(string='Bank Name/Bank Code/ROR No.')
    taxes_withheld = fields.Float(string='Taxes Withheld')
    penalties = fields.Float(string='Penalties')
    total_remitted = fields.Float(string='Total Amount Remitted', compute='_compute_total', store=True)

    @api.depends('taxes_withheld', 'penalties')
    def _compute_total(self):
        for record in self:
            record.total_remitted = record.taxes_withheld + record.penalties


class BIR1604ESchedule2(models.Model):
    _name = 'bir.1604e.schedule2'
    _description = 'BIR 1604-E Schedule 2 - Form 1606 Remittances'
    _order = 'form_id, month'

    form_id = fields.Many2one('bir.1604e', string='Form', required=True, ondelete='cascade')
    month = fields.Selection([
        ('JAN', 'January'),
        ('FEB', 'February'),
        ('MAR', 'March'),
        ('APR', 'April'),
        ('MAY', 'May'),
        ('JUN', 'June'),
        ('JUL', 'July'),
        ('AUG', 'August'),
        ('SEPT', 'September'),
        ('OCT', 'October'),
        ('NOV', 'November'),
        ('DEC', 'December'),
    ], string='Month', required=True)
    date_remittance = fields.Date(string='Date of Remittance')
    bank_name = fields.Char(string='Bank Name/Bank Code/ROR No.')
    taxes_withheld = fields.Float(string='Taxes Withheld')
    penalties = fields.Float(string='Penalties')
    total_remitted = fields.Float(string='Total Amount Remitted', compute='_compute_total', store=True)

    @api.depends('taxes_withheld', 'penalties')
    def _compute_total(self):
        for record in self:
            record.total_remitted = record.taxes_withheld + record.penalties


class BIR1604ESchedule3(models.Model):
    _name = 'bir.1604e.schedule3'
    _description = 'BIR 1604-E Schedule 3 - Exempt from Withholding'
    _order = 'form_id, sequence'

    form_id = fields.Many2one('bir.1604e', string='Form', required=True, ondelete='cascade')
    sequence = fields.Integer(string='Seq No.', required=True)
    tin = fields.Char(string='TIN')
    payee_name = fields.Char(string='Name of Payee', required=True)
    atc_id = fields.Many2one(
        'account.tax',
        string='ATC',
        domain=[('type_tax_use', '=', 'purchase')]
    )
    atc_code = fields.Char(string='ATC Code', related='atc_id.l10n_ph_atc', store=True)
    nature_income = fields.Char(string='Nature of Income Payment')
    income_payment = fields.Float(string='Amount of Income Payment')


class BIR1604ESchedule4(models.Model):
    _name = 'bir.1604e.schedule4'
    _description = 'BIR 1604-E Schedule 4 - Expanded Withholding Tax'
    _order = 'form_id, sequence'

    form_id = fields.Many2one('bir.1604e', string='Form', required=True, ondelete='cascade')
    sequence = fields.Integer(string='Seq No.', required=True)
    tin = fields.Char(string='TIN')
    payee_name = fields.Char(string='Name of Payee', required=True)
    atc_id = fields.Many2one(
        'account.tax',
        string='ATC',
        domain=[('type_tax_use', '=', 'purchase')]
    )
    atc_code = fields.Char(string='ATC Code', related='atc_id.l10n_ph_atc', store=True)
    nature_income = fields.Char(string='Nature of Income Payment')
    income_payment = fields.Float(string='Amount of Income Payment')
    tax_rate = fields.Float(string='Rate of Tax (%)')

    @api.onchange('atc_id')
    def _onchange_atc_id(self):
        if self.atc_id:
            self.tax_rate = self.atc_id.amount