from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class AccountMoveExpenseDv(models.Model):
    _inherit = 'account.move'

    dv_expense_ids = fields.One2many(
        comodel_name='hr.expense',
        inverse_name='dv_move_id',
        string="DV Expenses",
        readonly=True,
    )
    dv_expense_count = fields.Integer(compute='_compute_dv_expense_count')

    @api.depends('dv_expense_ids')
    def _compute_dv_expense_count(self):
        for move in self:
            # sudo: accounting users may not have rights on other employees' expenses
            move.dv_expense_count = len(move.sudo().dv_expense_ids)

    def action_view_dv_expenses(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Expenses on %s", self.display_name),
            'res_model': 'hr.expense',
            'view_mode': 'list,form',
            'domain': [('dv_move_id', '=', self.id)],
            'context': {'create': False},
        }

    def action_export_dv_summary(self):
        self.ensure_one()
        expenses = self.sudo().dv_expense_ids
        if not expenses:
            raise UserError(_("This bill has no expenses linked to it."))
        # Download only; the copy attached at creation stays as the original.
        attachment = expenses._dv_create_summary_attachment()
        return expenses._dv_download_action(attachment)

    def button_cancel(self):
        res = super().button_cancel()
        for move in self.filtered(lambda m: m.state == 'cancel'):
            expenses = move.sudo().dv_expense_ids
            if not expenses:
                continue
            names = Markup('').join(Markup('<li>%s – %s</li>') % (e.employee_id.name or '', e.name) for e in expenses)
            expenses.write({'dv_move_id': False})
            move.message_post(body=Markup('%s<ul>%s</ul>') % (
                _("DV cancelled. These expenses were released and can be put on a new DV:"), names))
        return res
