from odoo import api, models, fields
from odoo.exceptions import UserError
import base64


class CollectionReceipt(models.Model):
    _name = 'collection.receipt'
    _description = 'Collection Receipt'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _rec_name = 'name'
    _order = 'date desc, id desc'

    name = fields.Char(
        string='Reference',
        required=True,
        copy=False,
        readonly=True,
        default='New'
    )
    date = fields.Date(
        string='Date',
        default=fields.Date.context_today,
        required=True,
        tracking=True
    )
    customer_id = fields.Many2one(
        'res.partner',
        string='Customer',
        required=True,
        tracking=True
    )
    amount = fields.Monetary(
        string='Amount',
        currency_field='currency_id',
        tracking=True
    )
    currency_id = fields.Many2one(
        'res.currency',
        default=lambda self: self.env.company.currency_id
    )
    state = fields.Selection([
        ('draft', 'Draft'),
        ('confirmed', 'Confirmed'),
        ('cancelled', 'Cancelled'),
    ], string='Status', default='draft', tracking=True)

    # Link back to account.payment
    payment_id = fields.Many2one(
        'account.payment',
        string='Payment',
        readonly=True,
        copy=False
    )

    # Optional: company
    company_id = fields.Many2one(
        'res.company',
        default=lambda self: self.env.company
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'collection.receipt'
                ) or 'New'
        return super().create(vals_list)

    def action_confirm(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError("Only Draft records can be confirmed.")
            rec.state = 'confirmed'

    def action_cancel(self):
        for rec in self:
            if rec.state == 'cancelled':
                raise UserError("Record is already cancelled.")
            rec.state = 'cancelled'

    def action_set_to_draft(self):
        for rec in self:
            rec.state = 'draft'

    def action_generate_pdf(self):
        """Generate PDF using the existing payment-based report."""
        self.ensure_one()
        if not self.payment_id:
            return False

        # Use the EXISTING report from itc_internal_dev, pero i-render
        # gamit ang payment_id (kasi payment-based ang template)
        report = self.env.ref(
            'itc_internal_dev.action_report_custom_official_receipt',
            raise_if_not_found=False
        )
        if not report:
            return False

        pdf_content = self.env['ir.actions.report']._render_qweb_pdf(
            report.id, [self.payment_id.id]  # ← payment_id, hindi self.id
        )[0]

        # Remove old attachment if re-generating
        old = self.env['ir.attachment'].search([
            ('res_model', '=', 'collection.receipt'),
            ('res_id', '=', self.id),
            ('name', '=', f'CR_{self.name}.pdf'),
        ])
        if old:
            old.unlink()

        attachment = self.env['ir.attachment'].create({
            'name': f'CR_{self.name}.pdf',
            'type': 'binary',
            'datas': base64.b64encode(pdf_content),
            'res_model': 'collection.receipt',
            'res_id': self.id,
            'mimetype': 'application/pdf',
        })

        self.message_post(
            body="Collection Receipt PDF generated.",
            attachment_ids=[attachment.id],
            subtype_xmlid='mail.mt_comment'
        )
        return attachment
    
    def action_print(self):
        """Print the CR report (renders from linked payment)."""
        self.ensure_one()
        if not self.payment_id:
            raise UserError(
                "This Collection Receipt has no linked payment — cannot print."
            )
        return self.env.ref(
            'itc_internal_dev.action_report_custom_official_receipt'
        ).report_action(self.payment_id)