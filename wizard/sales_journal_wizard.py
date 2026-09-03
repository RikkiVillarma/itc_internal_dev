from odoo import fields, models


class SalesJournalWizard(models.TransientModel):
    _name = "sales.journal.wizard"
    _description = "Sales Journal Wizard"

    date_from = fields.Date(
        string="Date From",
        required=True,
        default=fields.Date.context_today,
    )

    date_to = fields.Date(
        string="Date To",
        required=True,
        default=fields.Date.context_today,
    )

    customer_id = fields.Many2one(
        "res.partner",
        string="Customer",
    )

    journal_id = fields.Many2one(
        "account.journal",
        string="Sales Journal",
        domain="[('type', '=', 'sale')]",
    )

    def action_generate(self):
        self.ensure_one()

        domain = [
            ("move_type", "=", "out_invoice"),
            ("state", "=", "posted"),
            ("invoice_date", ">=", self.date_from),
            ("invoice_date", "<=", self.date_to),
        ]

        if self.customer_id:
            domain.append(
                ("partner_id", "=", self.customer_id.id)
            )

        if self.journal_id:
            domain.append(
                ("journal_id", "=", self.journal_id.id)
            )

        invoices = self.env["account.move"].search(
            domain,
            order="invoice_date, name",
        )

        return {
            "type": "ir.actions.act_window",
            "name": "Sales Journal",
            "res_model": "account.move",
            "view_mode": "list,form",
            "domain": [("id", "in", invoices.ids)],
        }