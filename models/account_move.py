from odoo import models, fields, api
from odoo.exceptions import UserError, ValidationError
from odoo.tools import html2plaintext, date_utils


class AccountMove(models.Model):
    _inherit = "account.move"

    x_invoice_type = fields.Selection(
        [('service', 'Service'), ('consu', 'Sales')],
        string="Invoice Type",
        default='service',
    )

    x_partner_tin = fields.Char(
        string="Customer TIN",
        related='partner_id.vat',
        readonly=True,
        store=True,
    )

    x_reference_number = fields.Char(string="Reference Number", tracking=True)

    x_payee_ref = fields.Char(string="Payee Reference", tracking=True)

    x_journal_type = fields.Selection(
        related='journal_id.type',
        string='Journal Type',
    )

    # -------------------------------------------------------------------------
    # OVERRIDES
    # -------------------------------------------------------------------------
    def _post(self, soft=True):
        """
        Odoo 18 computes sequence numbers automatically via journal configuration.
        Do NOT override move.name with legacy ir.sequence inside _post().
        """
        posted = super()._post(soft=soft)
        return posted

    @api.model_create_multi
    def create(self, vals_list):
        """
        Use create_multi for Odoo 18 batch performance compatibility.
        """
        moves = super().create(vals_list)
        for move in moves:
            if move.invoice_origin:
                sale_order = self.env['sale.order'].search(
                    [('name', '=', move.invoice_origin)], limit=1
                )
                if sale_order:
                    move.x_reference_number = sale_order.name
                    move.x_invoice_type = sale_order.x_invoice_type or 'service'
        return moves

    # -------------------------------------------------------------------------
    # BIR 2307 REPORTING LOGIC
    # -------------------------------------------------------------------------
    def get_2307_details(self):
        self.ensure_one()

        if self.move_type != 'in_invoice':
            return []

        corp = self.company_id
        # 2307 period = the whole calendar month of the bill (e.g. Oct 8 -> Oct 1 to Oct 31)
        ref_date = self.invoice_date or self.date
        corp_data = {
            'corporation': corp,
            'period_from': date_utils.start_of(ref_date, 'month'),
            'period_to': date_utils.end_of(ref_date, 'month'),
            'lines': [],
            'gross_amount': 0.0,
            'tax_withheld': 0.0,
            'net_amount': 0.0,
        }

        # Odoo 18: real invoice lines have display_type == 'product'
        # (sections/notes are 'line_section' / 'line_note'), so `not display_type`
        # would skip every line.
        for line in self.invoice_line_ids.filtered(lambda l: l.display_type == 'product'):
            # Filter for withholding taxes on the line (flatten in case of tax groups)
            wht_taxes = line.tax_ids.flatten_taxes_hierarchy().filtered(
                lambda t: t.amount < 0 or 'wht' in (t.name or '').lower()
            )

            # Only income payments that were actually subject to withholding go on the 2307
            if not wht_taxes:
                continue

            gross = line.price_subtotal or 0.0

            # Compute taxes via Odoo tax logic (discount-aware, so it matches the bill)
            price_unit = line.price_unit * (1 - (line.discount or 0.0) / 100.0)
            tax_vals = line.tax_ids.compute_all(
                price_unit,
                currency=line.currency_id,
                quantity=line.quantity,
                product=line.product_id,
                partner=line.partner_id,
            )

            # Calculate actual tax withheld currency amount
            total_tax_amount = sum(
                t['amount'] for t in tax_vals['taxes']
                if t['id'] in wht_taxes.ids
            )

            tax_withheld = abs(total_tax_amount)
            net = gross - tax_withheld

            atc_codes = ', '.join(wht_taxes.mapped('name'))
            tax_rate = sum(abs(t.amount) for t in wht_taxes)

            # 2307 "Income Payments" column = the WHT tax's Description
            # (Advanced Options tab, an HTML field in Odoo 18), falling back to the tax name
            description = '; '.join(
                ' '.join(html2plaintext(t.description or '').split()) or t.name
                for t in wht_taxes
            )

            line_entry = {
                'description': description,
                'atc': atc_codes,
                'rate': tax_rate,
                'month': ref_date.month,
                'gross_amount': gross,
                'tax_withheld': tax_withheld,
                'net_amount': net,
                'tax_type': '',
                'payment_term': self.invoice_payment_term_id.name or '',
                'currency': self.currency_id.name,
                'conversion_rate': 1.0,
            }

            corp_data['lines'].append(line_entry)
            corp_data['gross_amount'] += gross
            corp_data['tax_withheld'] += tax_withheld
            corp_data['net_amount'] += net

        return [corp_data]

    def get_2307_business_details(self):
        return self.get_2307_details()

    def action_print(self):
        self.ensure_one()
        if self.state != 'posted':
            raise UserError("Invoice must be POSTED before generating BIR 2307.")

        return self.env.ref('itc_internal_dev.action_report_bir_2307').report_action(self)