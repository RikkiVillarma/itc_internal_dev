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

    # BIR invoice permit details (RR No. 006-2026, Annex B-1 item 13)
    x_bir_provider_name = fields.Char(string="Printer / Software Provider")
    x_bir_atp_no = fields.Char(string="ATP No. / OCN")
    x_bir_atp_date = fields.Date(string="ATP Date Issued")
    x_bir_series_range = fields.Char(string="Invoice Series Range")
    x_bir_accreditation_no = fields.Char(string="Accreditation No.")
    x_bir_accreditation_validity = fields.Char(string="Accreditation Validity")