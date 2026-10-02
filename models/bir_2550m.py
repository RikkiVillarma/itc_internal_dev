from odoo import models, fields, api
from odoo.exceptions import ValidationError, UserError
from datetime import date
import json
import calendar


# ============================================================
# SCHEDULE LINE MODELS
# ============================================================

class Bir2550mSch1Line(models.Model):
    """Schedule 1 - Sales/Receipts and Output Tax"""
    _name = "bir.2550m.sch1"
    _description = "2550M Schedule 1 - Vatable Sales"
    _order = "sequence, id"

    bir_id       = fields.Many2one("bir.2550m", ondelete="cascade", required=True)
    sequence     = fields.Integer(default=10)
    industry     = fields.Char(string="Industries Covered by VAT", required=True)
    atc          = fields.Char(string="ATC")
    sales_amount = fields.Monetary(string="Amount of Sales/Receipts", currency_field="currency_id")
    output_tax   = fields.Monetary(string="Output Tax", currency_field="currency_id",
                                   compute="_compute_output_tax", store=True)
    currency_id  = fields.Many2one(related="bir_id.currency_id", store=True)

    @api.depends("sales_amount", "industry")
    def _compute_output_tax(self):
        for rec in self:
            classification = (rec.industry or '').upper()
            is_non_vatable = 'ZERO' in classification or 'EXEMPT' in classification
            rec.output_tax = 0.0 if is_non_vatable else round(rec.sales_amount * 0.12, 2)


class Bir2550mSch2Line(models.Model):
    """Schedule 2 - Capital Goods ≤ ₱1M"""
    _name = "bir.2550m.sch2"
    _description = "2550M Schedule 2 - Capital Goods ≤ P1M"
    _order = "date_purchased, id"

    bir_id          = fields.Many2one("bir.2550m", ondelete="cascade", required=True)
    date_purchased  = fields.Date(string="Date Purchased")
    description     = fields.Char(string="Description")
    amount          = fields.Monetary(string="Amount (Net of VAT)", currency_field="currency_id")
    input_tax       = fields.Monetary(string="Input Tax", currency_field="currency_id",
                                      compute="_compute_input_tax", store=True)
    currency_id     = fields.Many2one(related="bir_id.currency_id", store=True)

    @api.depends("amount")
    def _compute_input_tax(self):
        for rec in self:
            rec.input_tax = round(rec.amount * 0.12, 2)


class Bir2550mSch3Line(models.Model):
    """Schedule 3 - Capital Goods > ₱1M"""
    _name = "bir.2550m.sch3"
    _description = "2550M Schedule 3 - Capital Goods > P1M"
    _order = "date_purchased, id"

    bir_id             = fields.Many2one("bir.2550m", ondelete="cascade", required=True)
    source_move_line_id = fields.Many2one("account.move.line", copy=False, index=True, ondelete="set null")
    is_previous_period = fields.Boolean(string="Previous Period", default=False,
                                        help="Check if this is a previous period purchase carried over.")
    date_purchased     = fields.Date(string="Date Purchased")
    description        = fields.Char(string="Description")
    amount             = fields.Monetary(string="Amount (Net of VAT)", currency_field="currency_id")
    input_tax          = fields.Monetary(string="Input Tax (C×12%)", currency_field="currency_id",
                                         compute="_compute_input_tax", store=True)
    est_life_months    = fields.Integer(string="Est. Life (months)")
    recognized_life    = fields.Integer(string="Recognized Life (months)",
                                        compute="_compute_recognized_life", store=True)
    allowable_input_tax = fields.Monetary(string="Allowable Input Tax (Period)",
                                          compute="_compute_allowable", store=True,
                                          currency_field="currency_id")
    balance_input_tax   = fields.Monetary(string="Balance of Input Tax (Next Period)",
                                          compute="_compute_allowable", store=True,
                                          currency_field="currency_id")
    currency_id         = fields.Many2one(related="bir_id.currency_id", store=True)

    @api.depends("amount")
    def _compute_input_tax(self):
        for rec in self:
            rec.input_tax = round(rec.amount * 0.12, 2)

    @api.depends("est_life_months")
    def _compute_recognized_life(self):
        for rec in self:
            rec.recognized_life = min(rec.est_life_months, 60) if rec.est_life_months else 0

    @api.depends("input_tax", "recognized_life")
    def _compute_allowable(self):
        for rec in self:
            if rec.recognized_life:
                rec.allowable_input_tax = round(rec.input_tax / rec.recognized_life, 2)
            else:
                rec.allowable_input_tax = 0.0
            rec.balance_input_tax = rec.input_tax - rec.allowable_input_tax


class Bir2550mSch4Line(models.Model):
    """Schedule 4 - Input Tax Attributable to Sale to Government"""
    _name = "bir.2550m.sch4"
    _description = "2550M Schedule 4 - Input Tax on Sales to Government"
    _order = "id"

    bir_id                = fields.Many2one("bir.2550m", ondelete="cascade", required=True)
    input_tax_direct      = fields.Monetary(string="Input Tax Directly Attributable to Govt Sales",
                                            currency_field="currency_id")
    taxable_sales_govt    = fields.Monetary(string="Taxable Sales to Government", currency_field="currency_id")
    total_sales           = fields.Monetary(string="Total Sales", currency_field="currency_id")
    input_tax_not_direct  = fields.Monetary(string="Input Tax Not Directly Attributable",
                                            currency_field="currency_id")
    ratable_portion       = fields.Monetary(string="Ratable Portion",
                                            compute="_compute_ratable", store=True,
                                            currency_field="currency_id")
    total_input_tax       = fields.Monetary(string="Total Input Tax Attributable to Govt",
                                            compute="_compute_total", store=True,
                                            currency_field="currency_id")
    standard_input_tax    = fields.Monetary(string="Less: Standard Input Tax to Govt",
                                            currency_field="currency_id")
    closed_to_expense     = fields.Monetary(string="Input Tax Closed to Expense",
                                            compute="_compute_closed", store=True,
                                            currency_field="currency_id")
    currency_id           = fields.Many2one(related="bir_id.currency_id", store=True)

    @api.depends("taxable_sales_govt", "total_sales", "input_tax_not_direct")
    def _compute_ratable(self):
        for rec in self:
            if rec.total_sales:
                rec.ratable_portion = round(
                    (rec.taxable_sales_govt / rec.total_sales) * rec.input_tax_not_direct, 2
                )
            else:
                rec.ratable_portion = 0.0

    @api.depends("input_tax_direct", "ratable_portion")
    def _compute_total(self):
        for rec in self:
            rec.total_input_tax = rec.input_tax_direct + rec.ratable_portion

    @api.depends("total_input_tax", "standard_input_tax")
    def _compute_closed(self):
        for rec in self:
            rec.closed_to_expense = rec.total_input_tax - rec.standard_input_tax


class Bir2550mSch5Line(models.Model):
    """Schedule 5 - Input Tax Attributable to Exempt Sales"""
    _name = "bir.2550m.sch5"
    _description = "2550M Schedule 5 - Input Tax on Exempt Sales"
    _order = "id"

    bir_id               = fields.Many2one("bir.2550m", ondelete="cascade", required=True)
    input_tax_direct     = fields.Monetary(string="Input Tax Directly Attributable to Exempt Sales",
                                           currency_field="currency_id")
    taxable_exempt_sale  = fields.Monetary(string="Taxable Exempt Sale", currency_field="currency_id")
    total_sales          = fields.Monetary(string="Total Sales", currency_field="currency_id")
    input_tax_not_direct = fields.Monetary(string="Input Tax Not Directly Attributable",
                                           currency_field="currency_id")
    ratable_portion      = fields.Monetary(string="Ratable Portion",
                                           compute="_compute_ratable", store=True,
                                           currency_field="currency_id")
    total_allocable      = fields.Monetary(string="Total Input Tax Allocable to Exempt",
                                           compute="_compute_total", store=True,
                                           currency_field="currency_id")
    currency_id          = fields.Many2one(related="bir_id.currency_id", store=True)

    @api.depends("taxable_exempt_sale", "total_sales", "input_tax_not_direct")
    def _compute_ratable(self):
        for rec in self:
            if rec.total_sales:
                rec.ratable_portion = round(
                    (rec.taxable_exempt_sale / rec.total_sales) * rec.input_tax_not_direct, 2
                )
            else:
                rec.ratable_portion = 0.0

    @api.depends("input_tax_direct", "ratable_portion")
    def _compute_total(self):
        for rec in self:
            rec.total_allocable = rec.input_tax_direct + rec.ratable_portion


class Bir2550mSch6Line(models.Model):
    """Schedule 6 - Creditable VAT Withheld (Tax Credit)"""
    _name = "bir.2550m.sch6"
    _description = "2550M Schedule 6 - Creditable VAT Withheld"
    _order = "id"

    bir_id              = fields.Many2one("bir.2550m", ondelete="cascade", required=True)
    source_payment_id   = fields.Many2one("account.payment", copy=False, index=True, ondelete="set null")
    period_covered      = fields.Char(string="Period Covered")
    withholding_agent   = fields.Char(string="Name of Withholding Agent")
    income_payment      = fields.Monetary(string="Income Payment", currency_field="currency_id")
    total_tax_withheld  = fields.Monetary(string="Total Tax Withheld", currency_field="currency_id")
    applied_current_mo  = fields.Monetary(string="Applied - Current Mo.", currency_field="currency_id")
    currency_id         = fields.Many2one(related="bir_id.currency_id", store=True)


class Bir2550mSch7Line(models.Model):
    """Schedule 7 - Advance Payments for Sugar and Flour"""
    _name = "bir.2550m.sch7"
    _description = "2550M Schedule 7 - Advance Payments (Sugar/Flour)"
    _order = "id"

    bir_id             = fields.Many2one("bir.2550m", ondelete="cascade", required=True)
    source_payment_id  = fields.Many2one("account.payment", copy=False, index=True, ondelete="set null")
    period_covered     = fields.Char(string="Period Covered")
    miller_name        = fields.Char(string="Name of Miller")
    taxpayer_name      = fields.Char(string="Taxpayer Name")
    or_number          = fields.Char(string="Official Receipt Number")
    amount_paid        = fields.Monetary(string="Amount Paid", currency_field="currency_id")
    applied_current_mo = fields.Monetary(string="Applied - Current Mo.", currency_field="currency_id")
    currency_id        = fields.Many2one(related="bir_id.currency_id", store=True)


class Bir2550mSch8Line(models.Model):
    """Schedule 8 - VAT Withheld on Sales to Government"""
    _name = "bir.2550m.sch8"
    _description = "2550M Schedule 8 - VAT Withheld on Govt Sales"
    _order = "id"

    bir_id             = fields.Many2one("bir.2550m", ondelete="cascade", required=True)
    period_covered     = fields.Char(string="Period Covered")
    withholding_agent  = fields.Char(string="Name of Withholding Agent")
    income_payment     = fields.Monetary(string="Income Payment", currency_field="currency_id")
    total_tax_withheld = fields.Monetary(string="Total Tax Withheld", currency_field="currency_id")
    applied_current_mo = fields.Monetary(string="Applied - Current Mo.", currency_field="currency_id")
    currency_id        = fields.Many2one(related="bir_id.currency_id", store=True)


# ============================================================
# MAIN MODEL
# ============================================================

class Bir2550M(models.Model):
    _name        = "bir.2550m"
    _description = "BIR 2550M - Monthly Value-Added Tax Declaration"
    _rec_name    = "display_name"
    _order       = "year desc, month desc, id desc"
    _inherit     = ["mail.thread", "mail.activity.mixin", "bir.hide.fields.mixin"]

    # ── Identity ──────────────────────────────────────────
    name = fields.Char(string="Reference", readonly=True, copy=False,
                       index=True, default="New")
    display_name = fields.Char(compute="_compute_display_name", store=True)

    company_id  = fields.Many2one("res.company", string="Company", required=True,
                                  default=lambda self: self.env.company, tracking=True)
    currency_id = fields.Many2one("res.currency", related="company_id.currency_id",
                                  store=True, readonly=True)

    year  = fields.Integer(string="Year", required=True,
                           default=lambda self: date.today().year, tracking=True)
    month = fields.Selection(
        selection=[
            ("1","January"),("2","February"),("3","March"),("4","April"),
            ("5","May"),("6","June"),("7","July"),("8","August"),
            ("9","September"),("10","October"),("11","November"),("12","December"),
        ],
        string="Month", required=True,
        default=lambda self: str(date.today().month), tracking=True)

    date_from = fields.Date(compute="_compute_dates", store=True)
    date_to   = fields.Date(compute="_compute_dates", store=True)

    is_amended       = fields.Boolean(string="Amended Return", default=False)
    number_of_sheets = fields.Integer(string="Number of Sheets Attached", default=0)

    # ── Background Info ────────────────────────────────────
    tin                = fields.Char(related="company_id.vat", readonly=True)
    rdo_code           = fields.Char(string="RDO Code", size=3)
    line_of_business   = fields.Char(string="Line of Business")
    registered_name    = fields.Char(related="company_id.name", readonly=True)
    telephone_number   = fields.Char(related="company_id.phone", readonly=True)
    zip_code           = fields.Char(related="company_id.zip", readonly=True)
    registered_address = fields.Text(compute="_compute_registered_address", store=True)
    has_tax_relief     = fields.Boolean(string="Tax Relief under Special Law/Treaty")
    tax_relief_specify = fields.Char(string="Specify Tax Relief")

    # ── State ─────────────────────────────────────────────
    state = fields.Selection(
        selection=[
            ("draft","Draft"), ("generated","Generated"),
            ("confirmed","Confirmed"), ("filed","Filed"), ("cancelled","Cancelled"),
        ],
        string="Status", default="draft", required=True, tracking=True, copy=False)

    date_confirmed = fields.Date(readonly=True, copy=False)
    date_filed     = fields.Date(readonly=True, copy=False)
    confirmed_by   = fields.Many2one("res.users", readonly=True, copy=False)
    filed_by       = fields.Many2one("res.users", readonly=True, copy=False)

    # ── Schedule One2many Lines ───────────────────────────
    sch1_ids = fields.One2many("bir.2550m.sch1", "bir_id", string="Schedule 1 - Vatable Sales")
    sch2_ids = fields.One2many("bir.2550m.sch2", "bir_id", string="Schedule 2 - Capital Goods ≤ ₱1M")
    sch3_ids = fields.One2many("bir.2550m.sch3", "bir_id", string="Schedule 3 - Capital Goods > ₱1M")
    sch4_ids = fields.One2many("bir.2550m.sch4", "bir_id", string="Schedule 4 - Sales to Govt Input Tax")
    sch5_ids = fields.One2many("bir.2550m.sch5", "bir_id", string="Schedule 5 - Exempt Sales Input Tax")
    sch6_ids = fields.One2many("bir.2550m.sch6", "bir_id", string="Schedule 6 - Creditable VAT Withheld")
    sch7_ids = fields.One2many("bir.2550m.sch7", "bir_id", string="Schedule 7 - Advance Payments")
    sch8_ids = fields.One2many("bir.2550m.sch8", "bir_id", string="Schedule 8 - VAT on Govt Sales")

    # =====================================================
    # PART II SUMMARY FIELDS
    # NOTE: Items 12–17 are driven by sch1_ids (loop in XML).
    #       Only Items 18–23 are individual fields below.
    # =====================================================

    # ── ITEM 17: Total Taxable Sales / Total Tax Due ──────
    item_17a = fields.Monetary(
        string="17A Total Taxable Sales",
        compute="_compute_item_17",
        currency_field="currency_id")
    item_17b = fields.Monetary(
        string="17B Total Tax Due",
        compute="_compute_item_17",
        currency_field="currency_id")

    # ── ITEM 18: Less: Input Taxes ────────────────────────
    item_18a = fields.Monetary(
        string="18A Transitional/Presumptive Input Tax",
        currency_field="currency_id")
    item_18b = fields.Monetary(
        string="18B Carried Over from Previous Return Period",
        compute="_compute_item_18b",
        currency_field="currency_id")
    item_18c = fields.Monetary(
        string="18C On Taxable Goods/Services",
        compute="_compute_item_18c",
        currency_field="currency_id")
    item_18d = fields.Monetary(
        string="18D Total Available Input Taxes",
        compute="_compute_item18d",
        currency_field="currency_id")
    item_18e = fields.Monetary(
        string="18E Less: Any Refund/TCC Claimed",
        currency_field="currency_id")
    item_18f = fields.Monetary(
        string="18F Net Creditable Input Tax",
        compute="_compute_item18f",
        currency_field="currency_id")

    # ── ITEM 19: VAT Payable (Excess Input Tax) ───────────
    item_19 = fields.Monetary(
        string="19 VAT Payable (Excess Input Tax)",
        compute="_compute_item19",
        currency_field="currency_id")

    # ── ITEM 20: Less: Tax Credits/Payments ───────────────
    item_20a = fields.Monetary(
        string="20A Advance Payments",
        compute="_compute_item_20abc",
        currency_field="currency_id")
    item_20b = fields.Monetary(
        string="20B Creditable Value-added Tax Withheld",
        compute="_compute_item_20abc",
        currency_field="currency_id")
    item_20c = fields.Monetary(
        string="20C VAT Paid in Return Previously Filed",
        compute="_compute_item_20abc",
        currency_field="currency_id")
    item_20d = fields.Monetary(
        string="20D Total Tax Credits/Payments",
        compute="_compute_item20d",
        currency_field="currency_id")

    # ── ITEM 21: Tax Payable/(Overpayment) ────────────────
    item_21 = fields.Monetary(
        string="21 Tax Payable/(Overpayment)",
        compute="_compute_item21",
        currency_field="currency_id")

    # ── ITEM 22: Penalties ────────────────────────────────
    item_22a = fields.Monetary(
        string="22A Surcharge",
        currency_field="currency_id")
    item_22b = fields.Monetary(
        string="22B Interest",
        currency_field="currency_id")
    item_22c = fields.Monetary(
        string="22C Compromise",
        currency_field="currency_id")
    item_22d = fields.Monetary(
        string="22D Total Penalties",
        compute="_compute_item22d",
        currency_field="currency_id")

    # ── ITEM 23: Total Amount Payable/(Overpayment) ───────
    item_23 = fields.Monetary(
        string="23 Total Amount Payable/(Overpayment)",
        compute="_compute_item23",
        currency_field="currency_id")

    # ── Payment Details ────────────────────────────────────
    payment_method = fields.Selection(
        selection=[
            ("cash", "Cash/Bank Debit Memo"),
            ("check", "Check"),
            ("tax_debit", "Tax Debit Memo"),
            ("others", "Others"),
        ], string="Payment Method")
    payment_bank   = fields.Char(string="Drawee Bank/Agency")
    payment_number = fields.Char(string="Payment Number")
    payment_date   = fields.Date(string="Payment Date")
    payment_amount = fields.Monetary(string="Payment Amount", currency_field="currency_id")

    notes = fields.Text(string="Internal Notes")

    # =====================================================
    # DECLARATION (Items 24–25)
    # =====================================================

    # ITEM 24 - President/VP/Authorized Representative/Tax Agent
    signatory_24_name  = fields.Char(string="24 Name of Signatory")
    signatory_24_title = fields.Char(string="24 Title/Position of Signatory")
    signatory_24_tin   = fields.Char(string="24 TIN of Tax Agent (if applicable)")

    # ITEM 25 - Treasurer/Asst. Treasurer/Authorized Representative
    signatory_25_name  = fields.Char(string="25 Name of Signatory")
    signatory_25_title = fields.Char(string="25 Title/Position of Signatory")
    signatory_25_accreditation = fields.Char(
        string="25 Tax Agent Accreditation No./Date of Accreditation (if applicable)")

    # =====================================================
    # PART III - DETAILS OF PAYMENT (Items 26–29)
    # =====================================================

    # ITEM 26 - Cash/Bank Debit Memo (AMOUNT ONLY)
    item_26_amount = fields.Monetary(string="26 Amount", currency_field="currency_id")

    # ITEM 27 - Check (Bank, Number, Date, Amount)
    item_27a_bank   = fields.Char(string="27A Drawee Bank/Agency")
    item_27b_number = fields.Char(string="27B Number")
    item_27c_date   = fields.Date(string="27C Date")
    item_27d_amount = fields.Monetary(string="27D Amount", currency_field="currency_id")

    # ITEM 28 - Tax Debit Memo (Number, Date, Amount — NO BANK)
    item_28a_number = fields.Char(string="28A Number")
    item_28b_date   = fields.Date(string="28B Date")
    item_28c_amount = fields.Monetary(string="28C Amount", currency_field="currency_id")

    # ITEM 29 - Others (Bank, Number, Date, Amount)
    item_29a_bank   = fields.Char(string="29A Drawee Bank/Agency")
    item_29b_number = fields.Char(string="29B Number")
    item_29c_date   = fields.Date(string="29C Date")
    item_29d_amount = fields.Monetary(string="29D Amount", currency_field="currency_id")

    # Machine Validation / Revenue Official Receipt Details
    machine_validation_details = fields.Text(
        string="Machine Validation/Revenue Official Receipt Details (If not filed with the bank)")

    # Stamp of Receiving Office
    receiving_office_stamp = fields.Text(string="Stamp of Receiving Office and Date of Receipt")

    # =====================================================
    # REPORT ACTIONS
    # =====================================================

    def action_generate_pdf(self):
        self.ensure_one()
        return self.env.ref('itc_internal_dev.action_report_custom_bir_2550m').report_action(self)

    def _get_pdf_filename(self):
        self.ensure_one()
        return 'BIR_2550M.pdf'

    def _generate_pdf_bytes(self):
        self.ensure_one()
        report = self.env.ref('itc_internal_dev.action_report_custom_bir_2550m')
        return self.env['ir.actions.report']._render_qweb_pdf(report.id, [self.id])[0]

    # =====================================================
    # COMPUTES - HEADER
    # =====================================================

    @api.depends("year", "month")
    def _compute_dates(self):
        for rec in self:
            if rec.year and rec.month:
                m = int(rec.month)
                last_day = calendar.monthrange(rec.year, m)[1]
                rec.date_from = date(rec.year, m, 1)
                rec.date_to   = date(rec.year, m, last_day)
            else:
                rec.date_from = rec.date_to = False

    @api.depends("name", "year", "month", "company_id")
    def _compute_display_name(self):
        month_names = {
            "1":"Jan","2":"Feb","3":"Mar","4":"Apr","5":"May","6":"Jun",
            "7":"Jul","8":"Aug","9":"Sep","10":"Oct","11":"Nov","12":"Dec",
        }
        for rec in self:
            seq = rec.name if rec.name and rec.name != "New" else "Draft"
            mon = month_names.get(rec.month or "1", "")
            rec.display_name = f"2550M | {rec.company_id.name or ''} | {mon} {rec.year} | {seq}"

    @api.depends("company_id")
    def _compute_registered_address(self):
        for rec in self:
            co = rec.company_id
            parts = filter(None, [
                co.street, co.street2, co.city,
                co.state_id.name if co.state_id else "",
                co.country_id.name if co.country_id else "",
            ])
            rec.registered_address = ", ".join(parts)

    @api.depends('sch6_ids.applied_current_mo',
             'sch7_ids.applied_current_mo',
             'sch8_ids.applied_current_mo')
    def _compute_item_20abc(self):
        for rec in self:
            rec.item_20a = sum(rec.sch7_ids.mapped('applied_current_mo'))
            rec.item_20b = sum(rec.sch6_ids.mapped('applied_current_mo'))
            rec.item_20c = sum(rec.sch8_ids.mapped('applied_current_mo'))
    
    @api.depends('date_from', 'date_to', 'company_id')
    def _compute_item_17(self):
        """Item 17A/17B = Output VAT from Sales Invoices (out_invoice)."""
        for rec in self:
            if not rec.date_from or not rec.date_to:
                rec.item_17a = rec.item_17b = 0.0
                continue

            AML = self.env['account.move.line']
            sales_lines = AML.search([
                ('account_id.account_type', 'in', ['income', 'income_other']),
                ('date', '>=', rec.date_from),
                ('date', '<=', rec.date_to),
                ('move_id.state', '=', 'posted'),
                ('move_id.move_type', 'in', ['out_invoice', 'out_refund']),
                ('company_id', '=', rec.company_id.id),
            ])
            output_tax_lines = AML.search([
                ('tax_line_id.amount', '=', 12),
                ('date', '>=', rec.date_from),
                ('date', '<=', rec.date_to),
                ('move_id.state', '=', 'posted'),
                ('move_id.move_type', 'in', ['out_invoice', 'out_refund']),
                ('company_id', '=', rec.company_id.id),
            ])

            taxable_sales = 0.0
            for line in sales_lines:
                if any(tax.amount == 12 for tax in line.tax_ids):
                    taxable_sales += line.credit - line.debit

            rec.item_17a = round(taxable_sales, 2)
            rec.item_17b = round(sum(-line.balance for line in output_tax_lines), 2)

    # =====================================================
    # COMPUTES - ITEMS 18 to 23
    # =====================================================

    @api.depends("item_18a", "item_18b", "item_18c")
    def _compute_item18d(self):
        for rec in self:
            rec.item_18d = rec.item_18a + rec.item_18b + rec.item_18c

    @api.depends("item_18d", "item_18e")
    def _compute_item18f(self):
        for rec in self:
            rec.item_18f = rec.item_18d - rec.item_18e

    @api.depends("item_17b", "item_18f")
    def _compute_item19(self):
        for rec in self:
            rec.item_19 = rec.item_17b - rec.item_18f

    @api.depends("item_20a", "item_20b", "item_20c")
    def _compute_item20d(self):
        for rec in self:
            rec.item_20d = rec.item_20a + rec.item_20b + rec.item_20c

    @api.depends("item_19", "item_20d")
    def _compute_item21(self):
        for rec in self:
            rec.item_21 = rec.item_19 - rec.item_20d

    @api.depends("item_22a", "item_22b", "item_22c")
    def _compute_item22d(self):
        for rec in self:
            rec.item_22d = rec.item_22a + rec.item_22b + rec.item_22c

    @api.depends("item_21", "item_22d")
    def _compute_item23(self):
        for rec in self:
            rec.item_23 = rec.item_21 + rec.item_22d

    # =====================================================
    # SYNC FROM SCHEDULES
    # =====================================================

    def _sync_from_schedules(self):
        """
        Push schedule totals into Part II summary fields.
        Items 12–17 are driven by sch1_ids (loop in XML), so no sync needed.
        Items 20A–20C pull from Sch 7, 6, 8 respectively.
        """
        for rec in self:
            rec.write({
                "item_20a": sum(rec.sch7_ids.mapped("applied_current_mo")),
                "item_20b": sum(rec.sch6_ids.mapped("applied_current_mo")),
                "item_20c": sum(rec.sch8_ids.mapped("applied_current_mo")),
            })

    # =====================================================
    # CONSTRAINTS
    # =====================================================

    @api.constrains("year")
    def _check_year(self):
        for rec in self:
            if rec.year < 2000 or rec.year > date.today().year + 1:
                raise ValidationError(
                    f"Year {rec.year} is not valid. "
                    f"Must be between 2000 and {date.today().year + 1}."
                )

    _sql_constraints = [
        (
            "unique_company_year_month",
            "UNIQUE(company_id, year, month)",
            "A BIR 2550M return for this company, year, and month already exists.",
        )
    ]

    # =====================================================
    # WORKFLOW ACTIONS
    # =====================================================

    def _get_month_name(self):
        months = {
            "1":"January","2":"February","3":"March","4":"April",
            "5":"May","6":"June","7":"July","8":"August",
            "9":"September","10":"October","11":"November","12":"December",
        }
        return months.get(self.month or "1", "")

    @api.depends('year', 'month', 'company_id')
    def _compute_item_18b(self):
        for rec in self:
            if not rec.year or not rec.month:
                rec.item_18b = 0.0
                continue

            prev_month = int(rec.month) - 1
            prev_year = rec.year
            if prev_month == 0:
                prev_month = 12
                prev_year -= 1

            prev_return = self.search([
                ('company_id', '=', rec.company_id.id),
                ('year', '=', prev_year),
                ('month', '=', str(prev_month)),
                ('state', 'in', ('confirmed', 'filed')),
            ], limit=1)

            if prev_return and prev_return.item_19 < 0:
                rec.item_18b = abs(prev_return.item_19)
            else:
                rec.item_18b = 0.0

    def _action_generate_data_legacy_direct(self):
        """
        Auto-generate Sch 1 (Vatable/Zero/Exempt) at Sch 2 (Capital Goods ≤ ₱1M)
        from posted journal entries.
        """
        for rec in self:
            if rec.state not in ("draft", "generated"):
                raise UserError("Only Draft or Generated returns can pull data. Reset to Draft first.")
            if not rec.date_from or not rec.date_to:
                raise ValidationError("Month/Year not properly set.")

            company_id = rec.company_id.id
            date_from  = rec.date_from
            date_to    = rec.date_to
            AML        = self.env["account.move.line"]

            # ─────────────────────────────────────────────
            # Sch 1: Sales by Tax Type (Vatable / Zero-Rated / Exempt)
            # ─────────────────────────────────────────────
            sales_lines = AML.search([
                ("account_id.account_type", "in", ["income", "income_other"]),
                ("date", ">=", date_from),
                ("date", "<=", date_to),
                ("move_id.state", "=", "posted"),
                ("move_id.move_type", "in", ["out_invoice", "out_refund"]),
                ("company_id", "=", company_id),
            ])

            rec.sch1_ids.unlink()

            if sales_lines:
                from collections import defaultdict
                by_tax_type = defaultdict(float)

                for sl in sales_lines:
                    net_amount = sl.credit - sl.debit

                    # Fallback kung walang tax_ids: treat as Vatable
                    if not sl.tax_ids:
                        by_tax_type['VATABLE'] += net_amount
                        continue

                    for tax in sl.tax_ids:
                        tax_name = (tax.name or '').upper()
                        if tax.amount == 12:
                            by_tax_type['VATABLE'] += net_amount
                        elif tax.amount == 0 and ('ZERO' in tax_name or 'ZR' in tax_name):
                            by_tax_type['ZERO_RATED'] += net_amount
                        elif tax.amount == 0 and 'EXEMPT' in tax_name:
                            by_tax_type['EXEMPT'] += net_amount

                sch1_vals = []
                for tax_type, amount in by_tax_type.items():
                    if amount > 0:
                        sch1_vals.append({
                            "bir_id": rec.id,
                            "industry": f"{tax_type} Sales",
                            "atc": "",
                            "sales_amount": round(amount, 2),
                        })
                if sch1_vals:
                    self.env["bir.2550m.sch1"].create(sch1_vals)

            # ─────────────────────────────────────────────
            # Sch 2: Capital Goods ≤ ₱1M
            # ─────────────────────────────────────────────
            capital_accounts = self.env["account.account"].search([
                ("account_type", "in", ["asset_fixed", "asset_non_current"]),
            ])

            rec.sch2_ids.unlink()
            if capital_accounts:
                cap_lines = AML.search([
                    ("account_id", "in", capital_accounts.ids),
                    ("date", ">=", date_from),
                    ("date", "<=", date_to),
                    ("move_id.state", "=", "posted"),
                    ("company_id", "=", company_id),
                    ("debit", ">", 0),
                ])
                sch2_vals = []
                for cl in cap_lines:
                    net_of_vat = round((cl.debit - cl.credit) / 1.12, 2)
                    if 0 < net_of_vat <= 1_000_000:
                        sch2_vals.append({
                            "bir_id": rec.id,
                            "date_purchased": cl.date,
                            "description": cl.name or cl.move_id.ref or cl.account_id.name,
                            "amount": net_of_vat,
                        })
                if sch2_vals:
                    self.env["bir.2550m.sch2"].create(sch2_vals)

            # ─────────────────────────────────────────────
            # Sync and Update State
            # ─────────────────────────────────────────────
            rec._sync_from_schedules()

            rec.state = "generated"
            rec.message_post(
                body=(
                    f"Data generated for {rec._get_month_name()} {rec.year}. "
                    f"Sch 1: {len(rec.sch1_ids)} line(s). "
                    f"Sch 2: {len(rec.sch2_ids)} line(s). "
                    "Please review and fill in Schedules 3–8 manually."
                )
            )   
    @api.depends(
        'date_from', 'date_to', 'company_id',
        'sch2_ids.input_tax',
        'sch3_ids.allowable_input_tax',
    )
    def _compute_item_18c(self):
        """Item 18C = Input VAT from vatable purchases + Sch 2 + Sch 3."""
        for rec in self:
            if not rec.date_from or not rec.date_to:
                rec.item_18c = 0.0
                continue

            AML = self.env['account.move.line']
            purchase_lines = AML.search([
                ('account_id.account_type', 'in',
                ['expense', 'expense_direct_cost', 'asset_current']),
                ('date', '>=', rec.date_from),
                ('date', '<=', rec.date_to),
                ('move_id.state', '=', 'posted'),
                ('move_id.move_type', 'in', ['in_invoice', 'in_refund']),
                ('company_id', '=', rec.company_id.id),
            ])

            input_vat = 0.0
            for line in purchase_lines:
                vat_taxes = line.tax_ids.filtered(lambda tax: tax.amount == 12)
                if not vat_taxes:
                    continue
                tax_values = vat_taxes.compute_all(
                    line.price_unit * (1 - (line.discount or 0.0) / 100),
                    currency=line.currency_id,
                    quantity=line.quantity,
                    product=line.product_id,
                    partner=line.partner_id,
                )
                line_vat = sum(tax['amount'] for tax in tax_values['taxes'])
                if line.move_id.move_type == 'in_refund':
                    line_vat = -line_vat
                input_vat += line.currency_id._convert(
                    line_vat, rec.currency_id, rec.company_id, line.date
                )

            input_vat += sum(rec.sch2_ids.mapped('input_tax'))
            input_vat += sum(rec.sch3_ids.mapped('allowable_input_tax'))

            rec.item_18c = round(input_vat, 2)
    
    def action_generate_sch6(self):
        """Pull creditable VAT withheld from customer payments."""
        for rec in self:
            if rec.state not in ('draft', 'generated'):
                raise UserError("Only Draft or Generated returns can pull data.")
            
            # Search customer payments within period
            Payment = self.env['account.payment']
            payments = Payment.search([
                ('payment_type', '=', 'inbound'),
                ('partner_type', '=', 'customer'),
                ('state', '=', 'paid'),
                ('date', '>=', rec.date_from),
                ('date', '<=', rec.date_to),
                ('company_id', '=', rec.company_id.id),
            ])
            
            values_by_payment = {}
            for pay in payments:
                vat_withheld = self._compute_vat_withheld_from_payment(pay)
                if vat_withheld > 0:
                    values_by_payment[pay.id] = {
                        'bir_id': rec.id,
                        'source_payment_id': pay.id,
                        'period_covered': f"{rec._get_month_name()} {rec.year}",
                        'withholding_agent': pay.partner_id.name,
                        'income_payment': pay.amount,
                        'total_tax_withheld': vat_withheld,
                        'applied_current_mo': vat_withheld,
                    }

            existing_lines = rec.sch6_ids.filtered('source_payment_id')
            stale_lines = existing_lines.filtered(lambda line: line.source_payment_id.id not in values_by_payment)
            if stale_lines:
                raise UserError(
                    "Schedule 6 has generated lines from payments outside the current period or eligibility rules. "
                    "Review those lines before regenerating."
                )

            existing_by_payment = {line.source_payment_id.id: line for line in existing_lines}
            for payment_id, values in values_by_payment.items():
                existing = existing_by_payment.get(payment_id)
                if existing:
                    existing.write(values)
                else:
                    self.env['bir.2550m.sch6'].create(values)
            
            rec._sync_from_schedules()
            
            rec.message_post(
                body=f"Schedule 6 synchronized from {len(values_by_payment)} payment(s)."
            )

    
    def action_generate_sch3(self):
        """Pull capital goods > ₱1M from asset accounts."""
        for rec in self:
            if rec.state not in ('draft', 'generated'):
                raise UserError("Only Draft or Generated returns can pull data.")
            
            AML = self.env['account.move.line']
            capital_accounts = self.env['account.account'].search([
                ('account_type', 'in', ['asset_fixed', 'asset_non_current']),
            ])
            
            cap_lines = AML.search([
                ('account_id', 'in', capital_accounts.ids),
                ('date', '>=', rec.date_from),
                ('date', '<=', rec.date_to),
                ('move_id.state', '=', 'posted'),
                ('move_id.move_type', '=', 'in_invoice'),
                ('company_id', '=', rec.company_id.id),
            ])
            cap_lines = cap_lines.filtered(lambda line: any(tax.amount == 12 for tax in line.tax_ids))
            source_lines = cap_lines.filtered(lambda line: line.balance > 1_000_000)
            existing_lines = rec.sch3_ids.filtered('source_move_line_id')
            stale_lines = existing_lines.filtered(lambda line: line.source_move_line_id not in source_lines)
            if stale_lines:
                raise UserError(
                    "Schedule 3 has generated lines from purchases outside the current period or eligibility rules. "
                    "Review those lines before regenerating."
                )

            existing_by_source = {line.source_move_line_id.id: line for line in existing_lines}
            for source_line in source_lines:
                values = {
                    'date_purchased': source_line.date,
                    'description': source_line.name or source_line.move_id.ref or source_line.account_id.name,
                    'amount': source_line.balance,
                }
                existing = existing_by_source.get(source_line.id)
                if existing:
                    existing.write(values)
                else:
                    self.env['bir.2550m.sch3'].create({
                        **values,
                        'bir_id': rec.id,
                        'source_move_line_id': source_line.id,
                        'est_life_months': 60,
                    })

            rec.message_post(
                body=f"Schedule 3 synchronized with {len(source_lines)} asset(s). "
                    f"Please review estimated life (months)."
            )

    def _compute_vat_withheld_from_payment(self, pay):
        """Extract VAT WHT from payment's withholding tax lines."""
        vat_withheld = 0.0

        if not pay.move_id:
            return 0.0

        for line in pay.move_id.line_ids:
            if line.tax_line_id and 'VAT' in (line.tax_line_id.name or '').upper():
                vat_withheld += abs(line.balance)

        return round(vat_withheld, 2)
    
    def action_generate_sch8(self):
        """Pull VAT withheld on government sales from customer payments."""
        for rec in self:
            if rec.state not in ('draft', 'generated'):
                raise UserError("Only Draft or Generated returns can pull data.")

            
            Payment = self.env['account.payment']
            payments = Payment.search([
                ('payment_type', '=', 'inbound'),
                ('partner_type', '=', 'customer'),
                ('state', '=', 'paid'),
                ('date', '>=', rec.date_from),
                ('date', '<=', rec.date_to),
                ('company_id', '=', rec.company_id.id),
            ])
            
            values_by_payment = {}
            for pay in payments:
                matched_invoices = pay.move_id._get_reconciled_invoices()
                government_invoices = matched_invoices.filtered(self._has_government_sales_tag)
                if not government_invoices or len(government_invoices) != len(matched_invoices):
                    continue

                vat_withheld = self._compute_vat_withheld_from_payment(pay)
                if vat_withheld <= 0:
                    continue

                values_by_payment[pay.id] = {
                    'bir_id': rec.id,
                    'source_payment_id': pay.id,
                    'period_covered': f"{rec._get_month_name()} {rec.year}",
                    'withholding_agent': pay.partner_id.name,
                    'income_payment': pay.amount,
                    'total_tax_withheld': vat_withheld,
                    'applied_current_mo': vat_withheld,
                }

            existing_lines = rec.sch8_ids.filtered('source_payment_id')
            stale_lines = existing_lines.filtered(lambda line: line.source_payment_id.id not in values_by_payment)
            if stale_lines:
                raise UserError(
                    "Schedule 8 has generated lines from payments outside the current period or eligibility rules. "
                    "Review those lines before regenerating."
                )

            existing_by_payment = {line.source_payment_id.id: line for line in existing_lines}
            for payment_id, values in values_by_payment.items():
                existing = existing_by_payment.get(payment_id)
                if existing:
                    existing.write(values)
                else:
                    self.env['bir.2550m.sch8'].create(values)
            
            rec._sync_from_schedules()
            
            rec.message_post(
                body=f"Schedule 8 synchronized from {len(values_by_payment)} government payment(s)."
            )

    @api.model
    def _has_government_sales_tag(self, move):
        return any(
            '32A' in (tag.name or '').upper()
            for line in move.invoice_line_ids
            for tag in line.tax_tag_ids
        )
    
    def action_generate_sch4(self):
        """Auto-fill Schedule 4 from vatable sales to government."""
        for rec in self:
            if rec.state not in ('draft', 'generated'):
                raise UserError("Only Draft or Generated returns can pull data.")


            # 1. Compute taxable_sales_govt from Schedule 1
            # Get all sales to government partners
            AML = self.env['account.move.line']
            
            # Sales to government (from out_invoice with gov partner)
            gov_sales = AML.search([
                ('account_id.account_type', 'in', ['income', 'income_other']),
                ('tax_tag_ids.name', 'ilike', '32A'),
                ('date', '>=', rec.date_from),
                ('date', '<=', rec.date_to),
                ('move_id.state', '=', 'posted'),
                ('move_id.move_type', 'in', ['out_invoice', 'out_refund']),
                ('company_id', '=', rec.company_id.id),
            ])
            
            taxable_sales_govt = sum(-line.balance for line in gov_sales)

            # 2. Compute total_sales from Schedule 1
            total_sales = sum(rec.sch1_ids.mapped('sales_amount'))

            values = {
                'taxable_sales_govt': taxable_sales_govt,
                'total_sales': total_sales,
            }
            if len(rec.sch4_ids) > 1:
                raise UserError("Schedule 4 has multiple rows; consolidate them before regenerating.")
            if rec.sch4_ids:
                rec.sch4_ids.write(values)
            elif taxable_sales_govt or total_sales:
                self.env['bir.2550m.sch4'].create({
                    'bir_id': rec.id,
                    **values,
                })

            rec.message_post(
                body=f"Schedule 4 populated. Taxable Sales to Govt: ₱{taxable_sales_govt:,.2f}"
            )
    
    def action_generate_sch5(self):
        """Auto-fill Schedule 5 from exempt sales."""
        for rec in self:
            if rec.state not in ('draft', 'generated'):
                raise UserError("Only Draft or Generated returns can pull data.")

            AML = self.env['account.move.line']
            
            # 1. Compute taxable_exempt_sale from Schedule 1
            exempt_sales = rec.sch1_ids.filtered(
                lambda l: 'EXEMPT' in (l.industry or '').upper()
            )
            taxable_exempt_sale = sum(exempt_sales.mapped('sales_amount'))

            # 2. total_sales from Schedule 1
            total_sales = sum(rec.sch1_ids.mapped('sales_amount'))

            values = {
                'taxable_exempt_sale': taxable_exempt_sale,
                'total_sales': total_sales,
            }
            if len(rec.sch5_ids) > 1:
                raise UserError("Schedule 5 has multiple rows; consolidate them before regenerating.")
            if rec.sch5_ids:
                rec.sch5_ids.write(values)
            elif taxable_exempt_sale or total_sales:
                self.env['bir.2550m.sch5'].create({
                    'bir_id': rec.id,
                    **values,
                })

            rec.message_post(
                body=f"Schedule 5 populated. Exempt Sales: ₱{taxable_exempt_sale:,.2f}"
            )

    # =====================================================
    # REPORTING-BASED DATA GENERATION
    # =====================================================

    def _get_generated_vat_sales_rows(self):
        """Read the JSON behind the generated VAT Summary List - Sales table."""
        self.ensure_one()
        report = self.env["custom.sql.report"].search([
            ("name", "=", "vat_summary_sales"),
            ("from_date", "=", self.date_from),
            ("to_date", "=", self.date_to),
        ], order="generated_on desc, id desc", limit=1)
        if not report or not report.result_ids:
            raise UserError(
                "Generate VAT Summary List - Sales in Reporting first for "
                f"{self.date_from} to {self.date_to}."
            )

        rows = []
        for line in report.result_ids:
            try:
                rows.extend(json.loads(line.data or "[]"))
            except (TypeError, ValueError):
                raise UserError("The generated VAT Summary List - Sales result is not valid JSON.")
        return rows

    @staticmethod
    def _sum_report_column(rows, column):
        """Sum formatted numeric values stored by custom.sql.report.line."""
        total = 0.0
        for row in rows:
            value = row.get(column, 0) or 0
            try:
                total += float(str(value).replace(",", "").strip() or 0)
            except (TypeError, ValueError):
                raise UserError(
                    f"Report column {column!r} contains a non-numeric value: {value!r}"
                )
        return total

    def action_generate_data(self):
        """Fill Schedule 1 sales from the generated VAT Summary List - Sales."""
        for rec in self:
            if rec.state not in ("draft", "generated"):
                raise UserError("Only Draft or Generated returns can pull data. Reset to Draft first.")
            if not rec.date_from or not rec.date_to:
                raise ValidationError("Month/Year not properly set.")

            sales_rows = rec._get_generated_vat_sales_rows()
            taxable_sales = (
                rec._sum_report_column(sales_rows, "AMOUNT OF TAXABLE SALES - PRIVATE")
                + rec._sum_report_column(sales_rows, "AMOUNT OF TAXABLE SALES - GOVERNMENT")
            )
            reported_output_tax = rec._sum_report_column(sales_rows, "AMOUNT OF OUTPUT TAX")
            schedule_output_tax = round(taxable_sales * 0.12, 2)

            # Schedule 1 currently computes tax at 12%; do not silently copy
            # report data that cannot be represented by that existing formula.
            if abs(schedule_output_tax - reported_output_tax) > 0.01:
                raise ValidationError(
                    "VAT Summary List - Sales output tax does not match the existing "
                    "2550M Schedule 1 12% calculation. No data was changed."
                )

            if len(rec.sch1_ids) > 1:
                raise UserError(
                    "This return has multiple Schedule 1 classifications. "
                    "Reporting provides only an aggregated taxable-sales total, so no data was changed."
                )

            if rec.sch1_ids:
                # Preserve the user-maintained Industry Classification and ATC.
                rec.sch1_ids.write({"sales_amount": taxable_sales})
            else:
                if not rec.line_of_business:
                    raise ValidationError(
                        "Set Line of Business before generating a new Schedule 1 line. "
                        "Reporting does not provide an Industry Classification."
                    )
                self.env["bir.2550m.sch1"].create({
                    "bir_id": rec.id,
                    "industry": rec.line_of_business,
                    "atc": "",
                    "sales_amount": taxable_sales,
                })

            rec.state = "generated"
            rec.message_post(
                body=(
                    "Schedule 1 taxable sales were populated from the generated "
                    "VAT Summary List - Sales. Industry Classification and ATC were preserved."
                )
            )

    def action_sync_from_schedules(self):
        """Manually push schedule totals into Part II summary fields."""
        for rec in self:
            if rec.state in ("confirmed", "filed", "cancelled"):
                raise UserError("Cannot sync a Confirmed, Filed, or Cancelled return.")
        self._sync_from_schedules()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Schedules Synced",
                "message": "Part II summary fields updated from schedule totals.",
                "type": "success",
                "sticky": False,
            }
        }

    def action_confirm(self):
        for rec in self:
            if rec.state != "generated":
                raise UserError("Only a Generated return can be confirmed.")
            if rec.name == "New":
                rec.name = self.env["ir.sequence"].next_by_code("bir.2550m") or "New"
            rec.state          = "confirmed"
            rec.date_confirmed = date.today()
            rec.confirmed_by   = self.env.user
            rec.message_post(body=f"Return confirmed by {self.env.user.name}. Reference: {rec.name}")

    def action_file(self):
        for rec in self:
            if rec.state != "confirmed":
                raise UserError("Only a Confirmed return can be filed.")
            rec.state      = "filed"
            rec.date_filed = date.today()
            rec.filed_by   = self.env.user
            rec.message_post(body=f"Return [{rec.name}] filed with BIR by {self.env.user.name}.")

    def action_reset_to_draft(self):
        for rec in self:
            if rec.state == "filed":
                raise UserError("A Filed return cannot be reset. Create an amended return.")
            if rec.state == "cancelled":
                raise UserError("A Cancelled return cannot be reset to Draft.")
            rec.state          = "draft"
            rec.date_confirmed = False
            rec.date_filed     = False
            rec.confirmed_by   = False
            rec.filed_by       = False
            rec.message_post(body=f"Return [{rec.name}] reset to Draft.")

    def action_cancel(self):
        for rec in self:
            if rec.state in ("confirmed", "filed"):
                raise UserError("Cannot cancel a Confirmed or Filed return.")
            rec.state = "cancelled"
            rec.message_post(body=f"Return [{rec.name}] cancelled.")

    def action_create_monthly_returns(self):
        """Batch-create all 12 months for this return's year. Skips existing."""
        self.ensure_one()
        target_year    = self.year
        target_company = self.company_id.id
        created = []
        skipped = []
        month_names = {
            "1":"Jan","2":"Feb","3":"Mar","4":"Apr","5":"May","6":"Jun",
            "7":"Jul","8":"Aug","9":"Sep","10":"Oct","11":"Nov","12":"Dec",
        }
        for m in range(1, 13):
            m_str = str(m)
            existing = self.search([
                ("company_id", "=", target_company),
                ("year", "=", target_year),
                ("month", "=", m_str),
            ], limit=1)
            if existing:
                skipped.append(month_names[m_str])
                continue
            self.create({
                "company_id": target_company,
                "year": target_year,
                "month": m_str,
                "state": "draft",
            })
            created.append(month_names[m_str])

        msg = f"Created: {', '.join(created) or 'none'}. Skipped existing: {', '.join(skipped) or 'none'}."
        self.message_post(body=msg)

        return {
            "type": "ir.actions.act_window",
            "name": f"BIR 2550M — {target_year}",
            "res_model": "bir.2550m",
            "view_mode": "list,form",
            "domain": [("company_id", "=", target_company), ("year", "=", target_year)],
        }
