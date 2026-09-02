from odoo import fields, models


class PurchaseSubsidiaryJournalWizard(models.TransientModel):
    _name = "purchase.subsidiary.journal.wizard"
    _description = "Purchase Subsidiary Journal Wizard"

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

        move_domain = [
            ("move_type", "=", "in_invoice"),
            ("state", "=", "posted"),
            ("invoice_date", ">=", self.date_from),
            ("invoice_date", "<=", self.date_to),
        ]

        if self.vendor_id:
            move_domain.append(
                ("partner_id", "=", self.vendor_id.id)
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
            "name": "Purchase Subsidiary Journal",
            "res_model": "account.move.line",
            "view_mode": "list,form",
            "views": [
                (
                    self.env.ref(
                        "itc_internal_dev.view_purchase_subsidiary_journal_result_list"
                    ).id,
                    "list",
                ),
                (False, "form"),
            ],
            "search_view_id": self.env.ref(
                "itc_internal_dev.view_purchase_subsidiary_journal_search"
            ).id,
            "domain": [
                ("id", "in", lines.ids),
            ],
            "context": {
                "create": False,
                "edit": False,
                "delete": False,
            },
        }