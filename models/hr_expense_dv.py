import io
import re
from collections import OrderedDict

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools.misc import format_date

try:
    import xlsxwriter
except ImportError:  # pragma: no cover
    xlsxwriter = None

XLSX_MIMETYPE = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

# Fields that may no longer change once an expense sits on a Disbursement Voucher,
# otherwise the DV and its expenses would silently drift apart.
DV_LOCKED_FIELDS = {
    'employee_id', 'product_id', 'date', 'quantity', 'price_unit',
    'total_amount_currency', 'total_amount', 'currency_id', 'tax_ids',
    'account_id', 'analytic_distribution', 'payment_mode', 'company_id',
}


class HrExpenseDv(models.Model):
    _inherit = 'hr.expense'

    dv_move_id = fields.Many2one(
        comodel_name='account.move',
        string="Disbursement Voucher",
        readonly=True,
        copy=False,
        index='btree_not_null',
        ondelete='set null',
        tracking=True,
        help="Disbursement Voucher (vendor bill) this expense was paid through.",
    )
    dv_payment_state = fields.Selection(
        related='dv_move_id.payment_state',
        string="DV Payment Status",
    )

    # -------------------------------------------------------------------------
    # Constraints / protection
    # -------------------------------------------------------------------------

    @api.constrains('sheet_id', 'dv_move_id')
    def _check_dv_sheet_exclusive(self):
        for expense in self:
            if expense.sheet_id and expense.dv_move_id:
                raise ValidationError(_(
                    "Expense \"%(expense)s\" is already on Disbursement Voucher %(dv)s "
                    "and cannot also be added to an expense report.",
                    expense=expense.name,
                    dv=expense.dv_move_id.display_name,
                ))

    def write(self, vals):
        if DV_LOCKED_FIELDS & set(vals):
            locked = self.filtered('dv_move_id')
            if locked:
                raise UserError(_(
                    "These expenses are already on a Disbursement Voucher and can no longer be changed:\n%(lines)s\n\n"
                    "Cancel the DV first if the expense needs correcting.",
                    lines="\n".join(f"• {e.name} ({e.dv_move_id.display_name})" for e in locked[:20]),
                ))
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _unlink_except_on_dv(self):
        locked = self.filtered('dv_move_id')
        if locked:
            raise UserError(_(
                "You cannot delete expenses that are on a Disbursement Voucher:\n%(lines)s",
                lines="\n".join(f"• {e.name} ({e.dv_move_id.display_name})" for e in locked[:20]),
            ))

    # -------------------------------------------------------------------------
    # Eligibility
    # -------------------------------------------------------------------------

    def _dv_check_eligible(self):
        """Raise if any expense in self cannot be put on a new DV."""
        if not self:
            raise UserError(_("Select at least one expense."))

        problems = []
        for expense in self:
            label = f"{expense.name} ({expense.employee_id.name or _('no employee')})"
            if expense.sheet_id:
                problems.append(_("%(exp)s is already in expense report %(sheet)s",
                                  exp=label, sheet=expense.sheet_id.name))
            elif expense.dv_move_id:
                problems.append(_("%(exp)s is already on Disbursement Voucher %(dv)s",
                                  exp=label, dv=expense.dv_move_id.display_name))
            elif expense.currency_id.is_zero(expense.total_amount_currency):
                problems.append(_("%(exp)s has a zero amount", exp=label))
            elif not expense.employee_id:
                problems.append(_("%(exp)s has no employee", exp=label))

        if problems:
            extra = len(problems) - 30
            msg = "\n".join(f"• {p}" for p in problems[:30])
            if extra > 0:
                msg += "\n" + _("…and %s more.", extra)
            raise UserError(_("These expenses can't go on a Disbursement Voucher:\n\n%s", msg))

        if len(self.company_id) > 1:
            raise UserError(_("All selected expenses must belong to the same company."))
        if len(self.currency_id) > 1:
            raise UserError(_(
                "All selected expenses must be in the same currency (found: %s).",
                ", ".join(self.currency_id.mapped('name')),
            ))

    # -------------------------------------------------------------------------
    # Actions (bound to the expense list via server actions)
    # -------------------------------------------------------------------------

    def action_open_dv_wizard(self):
        self._dv_check_eligible()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Create Disbursement Voucher"),
            'res_model': 'hr.expense.dv.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_expense_ids': [Command.set(self.ids)]},
        }

    def action_export_dv_summary(self):
        if not self:
            raise UserError(_("Select at least one expense to export."))
        attachment = self._dv_create_summary_attachment()
        return self._dv_download_action(attachment)

    # -------------------------------------------------------------------------
    # Excel summary
    # -------------------------------------------------------------------------

    def _dv_create_summary_attachment(self, voucher=None):
        filename, content = self._dv_build_summary_xlsx(voucher=voucher)
        vals = {
            'name': filename,
            'raw': content,
            'mimetype': XLSX_MIMETYPE,
        }
        if voucher:
            vals.update(res_model='account.move', res_id=voucher.id)
        return self.env['ir.attachment'].create(vals)

    @api.model
    def _dv_download_action(self, attachment):
        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{attachment.id}?download=true',
            'target': 'self',
        }

    def _dv_build_summary_xlsx(self, voucher=None):
        """Return (filename, xlsx bytes): expenses grouped per employee with
        subtotals and a grand total."""
        if xlsxwriter is None:
            raise UserError(_("The Python library xlsxwriter is required to export the summary."))

        expenses = self.sudo().sorted(
            lambda e: ((e.employee_id.name or '').lower(), e.date or fields.Date.today(), e.id)
        )
        company = expenses.company_id[:1] or self.env.company
        currency = expenses.currency_id[:1] or company.currency_id
        dates = [d for d in expenses.mapped('date') if d]
        date_from, date_to = (min(dates), max(dates)) if dates else (False, False)

        buf = io.BytesIO()
        wb = xlsxwriter.Workbook(buf, {'in_memory': True})
        ws = wb.add_worksheet('Expense Summary')

        decimals = currency.decimal_places
        money = '#,##0' + ('.' + '0' * decimals if decimals else '')
        f_title = wb.add_format({'bold': True, 'font_size': 14})
        f_sub = wb.add_format({'bold': True, 'font_size': 11})
        f_info = wb.add_format({'italic': True, 'font_color': '#555555'})
        f_head = wb.add_format({'bold': True, 'bg_color': '#D9E1F2', 'border': 1, 'text_wrap': True, 'valign': 'vcenter'})
        f_cell = wb.add_format({'border': 1})
        f_date = wb.add_format({'border': 1, 'num_format': 'yyyy-mm-dd'})
        f_money = wb.add_format({'border': 1, 'num_format': money})
        f_sub_lbl = wb.add_format({'bold': True, 'border': 1, 'bg_color': '#F2F2F2'})
        f_sub_money = wb.add_format({'bold': True, 'border': 1, 'bg_color': '#F2F2F2', 'num_format': money})
        f_tot_lbl = wb.add_format({'bold': True, 'border': 2, 'bg_color': '#FFF2CC'})
        f_tot_money = wb.add_format({'bold': True, 'border': 2, 'bg_color': '#FFF2CC', 'num_format': money})

        ws.write(0, 0, company.name, f_title)
        ws.write(1, 0, _("Expense Summary"), f_sub)
        if date_from:
            ws.write(2, 0, _("Period: %(start)s to %(end)s",
                             start=format_date(self.env, date_from),
                             end=format_date(self.env, date_to)), f_info)
        if voucher:
            dv_label = voucher.name if voucher.name and voucher.name != '/' else _("Draft")
            ws.write(3, 0, _("Disbursement Voucher: %(dv)s   Payee: %(payee)s   Ref: %(ref)s",
                             dv=dv_label, payee=voucher.partner_id.name or '',
                             ref=voucher.ref or ''), f_info)

        headers = [
            _("No."), _("Date"), _("Employee"), _("Description"), _("Category"),
            _("Account"), _("Untaxed (%s)", currency.name), _("Tax (%s)", currency.name),
            _("Total (%s)", currency.name),
        ]
        head_row = 5
        for col, title in enumerate(headers):
            ws.write(head_row, col, title, f_head)

        by_employee = OrderedDict()
        for expense in expenses:
            by_employee.setdefault(expense.employee_id, self.env['hr.expense'])
            by_employee[expense.employee_id] |= expense

        row = head_row + 1
        seq = 0
        subtotal_rows = []
        grand = [0.0, 0.0, 0.0]
        for employee, emp_expenses in by_employee.items():
            first_row = row
            sums = [0.0, 0.0, 0.0]
            for expense in emp_expenses:
                seq += 1
                amounts = [expense.untaxed_amount_currency, expense.tax_amount_currency, expense.total_amount_currency]
                account = expense._get_base_account()
                ws.write_number(row, 0, seq, f_cell)
                if expense.date:
                    ws.write_datetime(row, 1, fields.Datetime.to_datetime(expense.date), f_date)
                else:
                    ws.write_blank(row, 1, None, f_date)
                ws.write(row, 2, employee.name or '', f_cell)
                ws.write(row, 3, expense.name or '', f_cell)
                ws.write(row, 4, expense.product_id.display_name or '', f_cell)
                ws.write(row, 5, account.display_name or '', f_cell)
                for i, amount in enumerate(amounts):
                    ws.write_number(row, 6 + i, amount, f_money)
                    sums[i] += amount
                row += 1

            ws.merge_range(row, 0, row, 5, _("Subtotal – %s", employee.name or ''), f_sub_lbl)
            for i in range(3):
                col_letter = chr(ord('G') + i)
                ws.write_formula(row, 6 + i, f"=SUM({col_letter}{first_row + 1}:{col_letter}{row})",
                                 f_sub_money, currency.round(sums[i]))
                grand[i] += sums[i]
            subtotal_rows.append(row)
            row += 1

        ws.merge_range(row, 0, row, 5, _("GRAND TOTAL (%s expenses, %s employees)", seq, len(by_employee)), f_tot_lbl)
        for i in range(3):
            col_letter = chr(ord('G') + i)
            refs = ",".join(f"{col_letter}{r + 1}" for r in subtotal_rows) or "0"
            ws.write_formula(row, 6 + i, f"=SUM({refs})", f_tot_money, currency.round(grand[i]))

        for col, width in enumerate([6, 12, 28, 40, 24, 32, 16, 14, 16]):
            ws.set_column(col, col, width)
        ws.freeze_panes(head_row + 1, 0)
        ws.set_landscape()
        ws.fit_to_pages(1, 0)
        ws.repeat_rows(head_row)
        wb.close()

        if voucher:
            tag = voucher.name if voucher.name and voucher.name != '/' else (voucher.ref or f"draft_{voucher.id}")
        elif date_from:
            tag = f"{date_from}_{date_to}"
        else:
            tag = fields.Date.to_string(fields.Date.context_today(self))
        tag = re.sub(r'[^\w.-]+', '_', tag).strip('_')
        return f"Expense_Summary_{tag}.xlsx", buf.getvalue()