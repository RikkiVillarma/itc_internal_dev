from odoo import fields, models


class PurchaseJournalWizard(models.TransientModel):
    _name = "purchase.journal.wizard"
    _description = "Purchase Journal Wizard"

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

    vendor_id = fields.Many2one(
        "res.partner",
        string="Vendor",
    )

    journal_id = fields.Many2one(
        "account.journal",
        string="Purchase Journal",
        domain="[('type', '=', 'purchase')]",
    )

    def action_generate(self):
        self.ensure_one()

        domain = [
            ("move_type", "=", "in_invoice"),
            ("state", "=", "posted"),
            ("invoice_date", ">=", self.date_from),
            ("invoice_date", "<=", self.date_to),
        ]

        if self.vendor_id:
            domain.append(
                ("partner_id", "=", self.vendor_id.id)
            )

        if self.journal_id:
            domain.append(
                ("journal_id", "=", self.journal_id.id)
            )

        bills = self.env["account.move"].search(
            domain,
            order="invoice_date, name",
        )

        return {
            "type": "ir.actions.act_window",
            "name": "Purchase Journal",
            "res_model": "account.move",
            "view_mode": "list,form",
            "domain": [("id", "in", bills.ids)],
        }