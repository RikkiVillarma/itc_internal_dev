from odoo import fields, models


class SalesSubsidiaryJournalWizard(models.TransientModel):
    _name = "sales.subsidiary.journal.wizard"
    _description = "Sales Subsidiary Journal Wizard"

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

        move_domain = [
            ("move_type", "=", "out_invoice"),
            ("state", "=", "posted"),
            ("invoice_date", ">=", self.date_from),
            ("invoice_date", "<=", self.date_to),
        ]

        if self.customer_id:
            move_domain.append(
                ("partner_id", "=", self.customer_id.id)
            )

        if self.journal_id:
            move_domain.append(
                ("journal_id", "=", self.journal_id.id)
            )

        moves = self.env["account.move"].search(
            move_domain,
            order="invoice_date, name",
        )

        lines = self.env["account.move.line"].search(
            [
                ("move_id", "in", moves.ids),
            ],
            order="date, move_id, id",
        )

        return {
            "type": "ir.actions.act_window",
            "name": "Sales Subsidiary Journal",
            "res_model": "account.move.line",
            "view_mode": "list,form",
            "views": [
                (
                    self.env.ref(
                        "itc_internal_dev.view_sales_subsidiary_journal_result_list"
                    ).id,
                    "list",
                ),
                (False, "form"),
            ],
            "domain": [
                ("id", "in", lines.ids),
            ],
            "context": {
                "create": False,
                "edit": False,
                "delete": False,
            },
        }