from odoo import models, fields, api

class AccountTax(models.Model):
    _inherit = 'account.tax'

    amount_display = fields.Char(
        string='Amount',
        compute='_compute_amount_display',
    )

    x_vat_category = fields.Selection(
        selection=[
            ('vatable', 'VATable (12%)'),
            ('zero_rated', 'Zero-Rated'),
            ('exempt', 'VAT-Exempt'),
        ],
        string='BIR VAT Category',
        help='Used on printed invoices to split sales into VATable, Zero-Rated '
            'and VAT-Exempt. Leave empty for withholding taxes.',
    )


    @api.depends('amount', 'amount_type')
    def _compute_amount_display(self):
        for tax in self:
            if tax.amount_type == 'fixed':
                tax.amount_display = f"{tax.amount:,.2f}"
            elif tax.amount_type == 'group':
                tax.amount_display = ''
            else:
                tax.amount_display = f"{tax.amount:g}%"