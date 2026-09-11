from odoo import models, fields, api
from odoo.exceptions import ValidationError, UserError
from datetime import date
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

    @api.depends("sales_amount")
    def _compute_output_tax(self):
        for rec in self:
            rec.output_tax = round(rec.sales_amount * 0.12, 2)


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

    # ── ITEM 18: Less: Input Taxes ────────────────────────
    item_18a = fields.Monetary(
        string="18A Transitional/Presumptive Input Tax",
        currency_field="currency_id")
    item_18b = fields.Monetary(
        string="18B Carried Over from Previous Return Period",
        currency_field="currency_id")
    item_18c = fields.Monetary(
        string="18C On Taxable Goods/Services",
        currency_field="currency_id")
    item_18d = fields.Monetary(
        string="18D Total Available Input Taxes",
        compute="_compute_item18d", store=True,
        currency_field="currency_id")
    item_18e = fields.Monetary(
        string="18E Less: Any Refund/TCC Claimed",
        currency_field="currency_id")
    item_18f = fields.Monetary(
        string="18F Net Creditable Input Tax",
        compute="_compute_item18f", store=True,
        currency_field="currency_id")

    # ── ITEM 19: VAT Payable (Excess Input Tax) ───────────
    item_19 = fields.Monetary(
        string="19 VAT Payable (Excess Input Tax)",
        compute="_compute_item19", store=True,
        currency_field="currency_id")

    # ── ITEM 20: Less: Tax Credits/Payments ───────────────
    item_20a = fields.Monetary(
        string="20A Advance Payments",
        currency_field="currency_id")
    item_20b = fields.Monetary(
        string="20B Creditable Value-added Tax Withheld",
        currency_field="currency_id")
    item_20c = fields.Monetary(
        string="20C VAT Paid in Return Previously Filed",
        currency_field="currency_id")
    item_20d = fields.Monetary(
        string="20D Total Tax Credits/Payments",
        compute="_compute_item20d", store=True,
        currency_field="currency_id")

    # ── ITEM 21: Tax Payable/(Overpayment) ────────────────
    item_21 = fields.Monetary(
        string="21 Tax Payable/(Overpayment)",
        compute="_compute_item21", store=True,
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
        compute="_compute_item22d", store=True,
        currency_field="currency_id")

    # ── ITEM 23: Total Amount Payable/(Overpayment) ───────
    item_23 = fields.Monetary(
        string="23 Total Amount Payable/(Overpayment)",
        compute="_compute_item23", store=True,
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

    @api.depends("item_18f", "sch1_ids.output_tax")
    def _compute_item19(self):
        """
        Item 19 = Item 17B (Total Tax Due) less Item 18F (Net Creditable Input Tax)
        Item 17B = sum of sch1_ids.output_tax (the loop in XML).
        """
        for rec in self:
            total_tax_due = sum(rec.sch1_ids.mapped("output_tax")) or 0.0
            rec.item_19 = total_tax_due - rec.item_18f

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

    def action_generate_data(self):
        """
        Auto-generate Sch 1 (Vatable Sales) and Sch 2 (Capital Goods ≤ ₱1M)
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

            # ── Vatable Sales → Sch 1 ──
            sales_lines = AML.search([
                ("account_id.account_type", "in", ["income", "income_other"]),
                ("date", ">=", date_from), ("date", "<=", date_to),
                ("move_id.state", "=", "posted"),
                ("move_id.move_type", "in", ["out_invoice", "out_refund"]),
                ("company_id", "=", company_id),
                ("credit", ">", 0),
            ])

            rec.sch1_ids.unlink()
            if sales_lines:
                from collections import defaultdict
                by_account = defaultdict(float)
                for sl in sales_lines:
                    by_account[sl.account_id.name] += (sl.credit - sl.debit)

                sch1_vals = []
                for account_name, amount in by_account.items():
                    if amount > 0:
                        sch1_vals.append({
                            "bir_id": rec.id,
                            "industry": account_name,
                            "atc": "",
                            "sales_amount": round(amount, 2),
                        })
                if sch1_vals:
                    self.env["bir.2550m.sch1"].create(sch1_vals)

            # ── Capital Goods Purchases → Sch 2 ──
            capital_accounts = self.env["account.account"].search([
                ("account_type", "in", ["asset_fixed", "asset_non_current"]),
            ])

            rec.sch2_ids.unlink()
            if capital_accounts:
                cap_lines = AML.search([
                    ("account_id", "in", capital_accounts.ids),
                    ("date", ">=", date_from), ("date", "<=", date_to),
                    ("move_id.state", "=", "posted"),
                    ("company_id", "=", company_id),
                    ("debit", ">", 0),
                ])
                sch2_vals = []
                for cl in cap_lines:
                    net_of_vat = round((cl.debit - cl.credit) / 1.12, 2)
                    if net_of_vat > 0 and net_of_vat <= 1_000_000:
                        sch2_vals.append({
                            "bir_id": rec.id,
                            "date_purchased": cl.date,
                            "description": cl.name or cl.move_id.ref or cl.account_id.name,
                            "amount": net_of_vat,
                        })
                if sch2_vals:
                    self.env["bir.2550m.sch2"].create(sch2_vals)

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