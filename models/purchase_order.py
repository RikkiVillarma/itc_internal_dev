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

    withholding_tax_ids = fields.Many2many(
        'account.tax',
        'purchase_order_line_withholding_tax_rel',
        'order_line_id',
        'tax_id',
        string='Withholding Tax',
        domain=[('type_tax_use', '=', 'purchase')],
        context={'default_type_tax_use': 'purchase', 'search_view_ref': 'account.account_tax_view_search'},
    )

    def _is_withholding_tax(self, tax):
        """Placeholder criterion — amount < 0.
        TODO: replace with finalized identification logic (tax group / flag)."""
        return tax.amount < 0

    @api.onchange('withholding_tax_ids')
    def _onchange_withholding_tax_ids(self):
        for line in self:
            non_wht_taxes = line.taxes_id.filtered(lambda t: not line._is_withholding_tax(t))
            line.taxes_id = non_wht_taxes + line.withholding_tax_ids

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines._sync_withholding_into_taxes()
        return lines

    def write(self, vals):
        res = super().write(vals)
        if 'withholding_tax_ids' in vals:
            self._sync_withholding_into_taxes()
        return res

    def _sync_withholding_into_taxes(self):
        for line in self:
            non_wht_taxes = line.taxes_id.filtered(lambda t: not line._is_withholding_tax(t))
            new_taxes = non_wht_taxes + line.withholding_tax_ids
            if new_taxes != line.taxes_id:
                line.taxes_id = new_taxes