from odoo import models, fields, api
from datetime import date

class Purchase(models.Model):
    _inherit = 'purchase.order'

    x_vendor_tin = fields.Char(
        string="Vendor TIN",
        related='partner_id.vat',
        readonly=True,
        store=True,
        help="Tax Identification Number of the vendor associated with this purchase order."
    )

    x_noted_by = fields.Many2one(
        'res.users',
        string="Noted By",
        help="User who noted this purchase order.",
        tracking=True,
    )
    x_noted_by_date = fields.Date(
        string="Noted Date",
        readonly=True,
        store=True,
        help="Date when this purchase order was noted.",
    )

    x_checked_by = fields.Many2one(
        'res.users',
        string="Checked By",
        help="User who checked this purchase order.",
        tracking=True,
    )
    x_checked_by_date = fields.Date(
        string="Checked Date",
        readonly=True,
        store=True,
        help="Date when this purchase order was checked.",
    )

    x_remarks = fields.Text(
        string="Remarks",
        help="Remarks related to this purchase order."
    )

    x_purchase_type = fields.Selection(
        [
            ('import', 'Import'),
            ('local', 'Local'),
        ],
        string="Purchase Type",
        default='local',
        help="Indicates whether the purchase order is for Import or Local purposes."
    )


    # Show date in UI immediately
    @api.onchange('x_checked_by')
    def _onchange_checked_by(self):
        for record in self:
            if record.x_checked_by and not record.x_checked_by_date:
                record.x_checked_by_date = date.today()

    @api.onchange('x_noted_by')
    def _onchange_noted_by(self):
        for record in self:
            if record.x_noted_by and not record.x_noted_by_date:
                record.x_noted_by_date = date.today()

    @api.onchange('partner_id')
    def _onchange_partner_id(self):
        for record in self:
            record.x_vendor_tin = record.partner_id.vat or ''

    # Ensure dates are saved on backend
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('x_checked_by') and not vals.get('x_checked_by_date'):
                vals['x_checked_by_date'] = date.today()
            if vals.get('x_noted_by') and not vals.get('x_noted_by_date'):
                vals['x_noted_by_date'] = date.today()
        return super().create(vals_list)

    def write(self, vals):
        for rec in self:
            if vals.get('x_checked_by') and not rec.x_checked_by_date:
                vals['x_checked_by_date'] = date.today()
            if vals.get('x_noted_by') and not rec.x_noted_by_date:
                vals['x_noted_by_date'] = date.today()
        return super().write(vals)

class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    display_tax_ids = fields.Many2many(
        'account.tax',
        compute='_compute_split_taxes',
        inverse='_inverse_display_tax_ids',
        string='Taxes',
        domain=[('type_tax_use', '=', 'purchase')],
    )

    withholding_tax_ids = fields.Many2many(
        'account.tax',
        compute='_compute_split_taxes',
        inverse='_inverse_withholding_tax_ids',
        string='Withholding Tax',
        domain=[('type_tax_use', '=', 'purchase')],
    )

    def _is_withholding_tax(self, tax):
        """Placeholder criterion — amount < 0.
        TODO: replace with finalized identification logic (tax group / flag)."""
        return tax.amount < 0

    @api.depends('taxes_id')
    def _compute_split_taxes(self):
        for line in self:
            wht = line.taxes_id.filtered(line._is_withholding_tax)
            line.withholding_tax_ids = wht
            line.display_tax_ids = line.taxes_id - wht

    def _inverse_display_tax_ids(self):
        for line in self:
            line.taxes_id = line.display_tax_ids + line.withholding_tax_ids

    def _inverse_withholding_tax_ids(self):
        for line in self:
            line.taxes_id = line.display_tax_ids + line.withholding_tax_ids

    @api.onchange('display_tax_ids', 'withholding_tax_ids')
    def _onchange_split_taxes(self):
        for line in self:
            line.taxes_id = line.display_tax_ids + line.withholding_tax_ids

class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    display_tax_ids = fields.Many2many(
        'account.tax',
        compute='_compute_split_taxes',
        inverse='_inverse_display_tax_ids',
        string='Taxes',
        domain=[('type_tax_use', '=', 'purchase')],
    )

    withholding_tax_ids = fields.Many2many(
        'account.tax',
        compute='_compute_split_taxes',
        inverse='_inverse_withholding_tax_ids',
        string='Withholding Taxes',
        domain=[('type_tax_use', '=', 'purchase')],
    )

    def _is_withholding_tax(self, tax):
        """Placeholder criterion — amount < 0.
        TODO: replace with finalized identification logic (tax group / flag)."""
        return tax.amount < 0

    @api.depends('tax_ids')
    def _compute_split_taxes(self):
        for line in self:
            wht = line.tax_ids.filtered(line._is_withholding_tax)
            line.withholding_tax_ids = wht
            line.display_tax_ids = line.tax_ids - wht

    def _inverse_display_tax_ids(self):
        for line in self:
            line.tax_ids = line.display_tax_ids + line.withholding_tax_ids

    def _inverse_withholding_tax_ids(self):
        for line in self:
            line.tax_ids = line.display_tax_ids + line.withholding_tax_ids