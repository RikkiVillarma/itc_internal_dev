from odoo import models, fields

class ResPartner(models.Model):
    _inherit = 'res.partner'

    x_vat_status = fields.Selection(
        [('vat', 'VAT Registered'), ('non_vat', 'Non-VAT')],
        string='VAT Status', default='vat', tracking=True)