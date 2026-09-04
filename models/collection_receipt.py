from odoo import fields, models, api


class CollectionReceipt(models.Model):
    _name = "collection.receipt"
    _description = "Collection Receipt"

    name = fields.Char(string="Receipt Number", required=True, copy=False, readonly=True,
                       default=lambda self: self.env['ir.sequence'].next_by_code('collection.receipt'))
    date = fields.Date(string="Date", required=True, default=fields.Date.context_today)
    customer_id = fields.Many2one('res.partner', string="Customer", required=True)
    amount = fields.Float(string="Amount", required=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('confirmed', 'Confirmed'),
        ('cancelled', 'Cancelled')
    ], string="Status", default='draft', readonly=True)

    @api.model
    def create(self, vals):
        if vals.get('name', 'New') == 'New':
            vals['name'] = self.env['ir.sequence'].next_by_code('collection.receipt') or 'New'
        return super(CollectionReceipt, self).create(vals)

    def action_confirm(self):
        self.state = 'confirmed'

    def action_cancel(self):
        self.state = 'cancelled'

    def action_set_to_draft(self):
        self.state = 'draft'