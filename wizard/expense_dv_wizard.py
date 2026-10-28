from markupsafe import Markup

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools.misc import format_date


class HrExpenseDvWizard(models.TransientModel):
    _name = 'hr.expense.dv.wizard'
    _description = "Create Disbursement Voucher from Expenses"

    expense_ids = fields.Many2many('hr.expense', string="Expenses", required=True)

    # --- summary of the selection -------------------------------------------
    company_id = fields.Many2one('res.company', compute='_compute_summary', store=True)
    currency_id = fields.Many2one('res.currency', compute='_compute_summary', store=True)
    expense_count = fields.Integer(compute='_compute_summary', string="Expenses Selected")
    employee_count = fields.Integer(compute='_compute_summary', string="Employees")
    date_from = fields.Date(compute='_compute_summary', string="From")
    date_to = fields.Date(compute='_compute_summary', string="To")
    total_amount = fields.Monetary(compute='_compute_summary', string="Total", currency_field='currency_id')

    # --- voucher options ----------------------------------------------------
    payee_mode = fields.Selection(
        selection=[
            ('single', "One DV for everything, one payee"),
            ('employee', "One DV per employee (employee is the payee)"),
        ],
        string="Create",
        default='single',
        required=True,
    )
    partner_id = fields.Many2one(
        'res.partner',
        string="Payee",
        help="Who the single DV is payable to, e.g. the cashier/custodian releasing the funds.",
    )
    journal_id = fields.Many2one(
        'account.journal',
        string="Journal",
        compute='_compute_journal_id', store=True, readonly=False,
        domain="[('type', '=', 'purchase'), ('company_id', '=', company_id)]",
    )
    invoice_date = fields.Date(string="Bill Date", default=fields.Date.context_today, required=True)
    ref = fields.Char(string="Reference", compute='_compute_ref', store=True, readonly=False)
    grouping = fields.Selection(
        selection=[
            ('expense', "One line per expense"),
            ('employee_account', "One line per employee & account"),
            ('account', "One line per account"),
        ],
        string="DV Lines",
        default='expense',
        required=True,
        help="Lines are only merged when account, taxes and analytic distribution are identical, "
             "so the accounting stays the same whichever option you pick.",
    )
    attach_summary = fields.Boolean(
        string="Attach Excel summary to the DV",
        default=True,
    )

    @api.depends('expense_ids')
    def _compute_summary(self):
        for wizard in self:
            expenses = wizard.expense_ids
            dates = [d for d in expenses.mapped('date') if d]
            wizard.company_id = expenses.company_id[:1] or self.env.company
            wizard.currency_id = expenses.currency_id[:1] or wizard.company_id.currency_id
            wizard.expense_count = len(expenses)
            wizard.employee_count = len(expenses.employee_id)
            wizard.date_from = min(dates) if dates else False
            wizard.date_to = max(dates) if dates else False
            wizard.total_amount = sum(expenses.mapped('total_amount_currency'))

    @api.depends('company_id')
    def _compute_journal_id(self):
        for wizard in self:
            if wizard.journal_id and wizard.journal_id.company_id == wizard.company_id:
                continue
            wizard.journal_id = self.env['account.journal'].search([
                ('type', '=', 'purchase'),
                ('company_id', '=', wizard.company_id.id),
            ], limit=1)

    @api.depends('date_from', 'date_to')
    def _compute_ref(self):
        for wizard in self:
            if not wizard.date_from:
                wizard.ref = _("Employee expenses")
            elif wizard.date_from == wizard.date_to:
                wizard.ref = _("Employee expenses %s", format_date(self.env, wizard.date_from))
            else:
                wizard.ref = _("Employee expenses %(start)s - %(end)s",
                               start=format_date(self.env, wizard.date_from),
                               end=format_date(self.env, wizard.date_to))

    # -------------------------------------------------------------------------
    # Buttons
    # -------------------------------------------------------------------------

    def action_export_summary(self):
        self.ensure_one()
        attachment = self.expense_ids._dv_create_summary_attachment()
        return self.expense_ids._dv_download_action(attachment)

    def action_create_dv(self):
        self.ensure_one()
        expenses = self.expense_ids
        expenses._dv_check_eligible()

        if not self.journal_id:
            raise UserError(_("Set a purchase journal for the Disbursement Voucher."))
        if self.payee_mode == 'single' and not self.partner_id:
            raise UserError(_("Choose who the Disbursement Voucher is payable to."))

        batches = self._get_batches(expenses)

        Move = self.env['account.move'].with_company(self.company_id)
        moves = Move.browse()
        for partner, batch_expenses in batches:
            move = Move.create(self._prepare_move_vals(partner, batch_expenses))
            # sudo: the accountant may not have write access on other employees' expenses
            batch_expenses.sudo().write({'dv_move_id': move.id})
            if self.attach_summary:
                batch_expenses._dv_create_summary_attachment(voucher=move)
            dates = [d for d in batch_expenses.mapped('date') if d]
            body = Markup("%s") % _("Created from %(count)s expenses (%(employees)s employees).",
                                    count=len(batch_expenses), employees=len(batch_expenses.employee_id))
            if dates:
                body += Markup("<br/>%s") % _("Period: %(start)s to %(end)s",
                                              start=format_date(self.env, min(dates)),
                                              end=format_date(self.env, max(dates)))
            move.message_post(body=body)
            moves |= move

        return self._action_open_moves(moves)

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _get_batches(self, expenses):
        """Return [(payee partner, expenses)] — one entry per DV to create."""
        if self.payee_mode == 'single':
            return [(self.partner_id, expenses)]

        batches, missing = [], []
        for employee in expenses.employee_id.sorted('name'):
            partner = employee.sudo().work_contact_id
            if not partner:
                missing.append(employee.name)
                continue
            batches.append((partner, expenses.filtered(lambda e, emp=employee: e.employee_id == emp)))
        if missing:
            raise UserError(_(
                "These employees have no work contact, so they can't be the payee of a DV:\n%s",
                "\n".join(f"• {name}" for name in missing),
            ))
        return batches

    def _prepare_move_vals(self, partner, expenses):
        return {
            'move_type': 'in_invoice',
            'company_id': self.company_id.id,
            'journal_id': self.journal_id.id,
            'partner_id': partner.id,
            'invoice_date': self.invoice_date,
            'date': self.invoice_date,
            'ref': self.ref,
            'currency_id': self.currency_id.id,
            'invoice_line_ids': [Command.create(vals) for vals in self._prepare_line_vals(expenses)],
        }

    def _prepare_line_vals(self, expenses):
        groups = OrderedDictLike()
        for expense in expenses.sudo().sorted(
            lambda e: ((e.employee_id.name or '').lower(), e.date or fields.Date.today(), e.id)
        ):
            account = expense._get_base_account()
            if not account:
                raise UserError(_("No expense account found for \"%s\". Set one on the expense category.", expense.name))
            taxes = expense.tax_ids
            if self.grouping == 'expense':
                key = ('expense', expense.id)
            else:
                distribution = tuple(sorted((expense.analytic_distribution or {}).items()))
                key = (account.id, tuple(sorted(taxes.ids)), distribution)
                if self.grouping == 'employee_account':
                    key = (expense.employee_id.id,) + key
            group = groups.get_or_create(key, lambda: {
                'expenses': self.env['hr.expense'],
                'account': account,
                'taxes': taxes,
                'distribution': expense.analytic_distribution,
                'untaxed': 0.0,
                'total': 0.0,
            })
            group['expenses'] |= expense
            group['untaxed'] += expense.untaxed_amount_currency
            group['total'] += expense.total_amount_currency

        vals_list = []
        for group in groups.values():
            group_expenses = group['expenses']
            taxes = group['taxes']
            # Expense amounts are always tax-included; a bill line is tax-excluded
            # unless its taxes are configured as price-included.
            price_include = bool(taxes) and all(taxes.mapped('price_include'))
            price_unit = group['total'] if price_include else group['untaxed']
            vals = {
                'name': self._line_label(group_expenses, group['account']),
                'account_id': group['account'].id,
                'quantity': 1.0,
                'price_unit': self.currency_id.round(price_unit),
                'tax_ids': [Command.set(taxes.ids)],
                'analytic_distribution': group['distribution'] or False,
            }
            # Keep the product when unambiguous (VAT summary reports rely on product type).
            if len(group_expenses.product_id) == 1:
                vals['product_id'] = group_expenses.product_id.id
            vals_list.append(vals)
        return vals_list

    def _line_label(self, expenses, account):
        if len(expenses) == 1:
            expense = expenses
            return f"{expense.employee_id.name} - {expense.name} ({format_date(self.env, expense.date)})"
        if self.grouping == 'employee_account':
            return _("%(employee)s - %(account)s (%(count)s expenses)",
                     employee=expenses.employee_id[:1].name, account=account.name, count=len(expenses))
        return _("%(account)s (%(count)s expenses, %(employees)s employees)",
                 account=account.name, count=len(expenses), employees=len(expenses.employee_id))

    def _action_open_moves(self, moves):
        action = self.env['ir.actions.act_window']._for_xml_id('account.action_move_in_invoice_type')
        if len(moves) == 1:
            action.update({
                'views': [(self.env.ref('account.view_move_form').id, 'form')],
                'view_mode': 'form',
                'res_id': moves.id,
            })
        else:
            action.update({
                'domain': [('id', 'in', moves.ids)],
                'name': _("Disbursement Vouchers"),
            })
        action['context'] = {'default_move_type': 'in_invoice'}
        return action


class OrderedDictLike(dict):
    """Insertion-ordered dict with a get_or_create helper (dicts keep order in py3.7+)."""

    def get_or_create(self, key, factory):
        if key not in self:
            self[key] = factory()
        return self[key]
