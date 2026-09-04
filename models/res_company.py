from odoo import models, fields


class ResCompany(models.Model):
    _inherit = 'res.company'

    withholding_tax_account_id = fields.Many2one(
        'account.account',
        string='Withholding Tax Account',
        help='Creditable Withholding Tax account used when finance enters WHT at payment time.',
    )