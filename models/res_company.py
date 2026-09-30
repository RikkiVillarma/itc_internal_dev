from odoo import models, fields


class ResCompany(models.Model):
    _inherit = 'res.company'

    withholding_tax_account_id = fields.Many2one(
        'account.account',
        string='Withholding Tax Account',
        help='Creditable Withholding Tax account used when finance enters WHT at payment time.',
    )

    # BIR permit details for the PO report
    x_bir_permit_no = fields.Char(string="BIR Permit No.")
    x_bir_permit_date = fields.Date(string="Date Issued")
    x_bir_lpo_range = fields.Char(string="LPO Series Range",
                                  help="e.g. LPO-000000000001 - LPO-999999999999")
    x_bir_ipo_range = fields.Char(string="IPO Series Range",
                                  help="e.g. IPO-000000000001 - IPO-999999999999")