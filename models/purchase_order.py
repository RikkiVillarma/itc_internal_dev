from odoo import models, fields, api, _
from odoo.exceptions import UserError
from datetime import date
import re

# Used to classify a PO line's VAT treatment from its (non-withholding) tax names,
# e.g. "0% ZR" -> zero-rated, "0% EXEMPT" -> VAT-exempt.
_ZERO_RATED_RE = re.compile(r'\b(ZR|ZERO[\s-]?RATED)\b', re.IGNORECASE)
_EXEMPT_RE = re.compile(r'\bEXEMPT\b', re.IGNORECASE)


class Purchase(models.Model):
    _inherit = 'purchase.order'

    def print_quotation(self):
        return self.env.ref(
            'itc_internal_dev.action_report_purchase_quotation_custom'
        ).report_action(self)

    def action_print_bir_2307(self):
        """Print BIR 2307 for the vendor bills of this PO.

        The 2307 report (itc_internal_dev.action_report_bir_2307) is bound to
        account.move and reads get_2307_details() from each bill, so the PO
        just collects its posted vendor bills and hands them to that report.
        One page per bill (per payor), same as printing from the bill itself.
        """
        bills = self.invoice_ids.filtered(
            lambda m: m.move_type == 'in_invoice' and m.state == 'posted'
        )
        if not bills:
            raise UserError(_(
                "There is no posted vendor bill for this purchase order yet. "
                "Post the bill first, then print the BIR 2307."
            ))
        return self.env.ref('itc_internal_dev.action_report_bir_2307').report_action(bills)

    def get_vat_breakdown(self):
        """VAT summary for the IPO/LPO printout.

        Each line's untaxed subtotal goes to Vatable / VAT-Exempt / Zero-Rated
        according to its VAT tax (see PurchaseOrderLine.get_vat_category).
        vat_amount is VAT only: withholding taxes on the line are left out,
        unlike amount_tax, which nets them in.
        """
        self.ensure_one()
        res = {'vatable': 0.0, 'exempt': 0.0, 'zero_rated': 0.0, 'vat_amount': 0.0}
        currency = self.currency_id
        for line in self.order_line.filtered(lambda l: not l.display_type):
            res[line.get_vat_category()] += line.price_subtotal

            vat_taxes = line.taxes_id.filtered(lambda t: not line._is_withholding_tax(t))
            if vat_taxes:
                taxes_res = vat_taxes.compute_all(
                    line.price_unit * (1 - (line.discount or 0.0) / 100.0),
                    currency=currency,
                    quantity=line.product_qty,
                    product=line.product_id,
                    partner=self.partner_id,
                )
                res['vat_amount'] += sum(t['amount'] for t in taxes_res['taxes'])
        return {key: currency.round(value) for key, value in res.items()}

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

    def get_vat_category(self):
        """Return 'vatable', 'zero_rated' or 'exempt' for this line.

        Looks only at the line's VAT taxes (withholding taxes excluded):
          - any VAT tax with a positive rate (e.g. "12% S")  -> vatable
          - a tax named like "0% ZR" / "Zero-Rated"          -> zero_rated
          - a tax named like "0% EXEMPT"                     -> exempt
          - no VAT tax at all                                -> exempt
        """
        self.ensure_one()
        vat_taxes = self.taxes_id.filtered(lambda t: not self._is_withholding_tax(t))
        if any(tax.amount > 0 for tax in vat_taxes):
            return 'vatable'
        names = ' '.join(vat_taxes.mapped('name'))
        if _ZERO_RATED_RE.search(names):
            return 'zero_rated'
        return 'exempt'

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