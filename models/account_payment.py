from odoo import api, fields, models, Command, _
from odoo.exceptions import ValidationError, UserError
import logging
_logger = logging.getLogger(__name__)


def _set_line_amounts(vals, amount_currency, balance):
    """Set every amount key of a move line vals dict consistently.

    Odoo 18 move lines are driven by 'balance': when a vals dict has both
    'balance' and 'debit'/'credit', the debit/credit keys are discarded.
    Payment lines from super() carry 'balance', so changing only debit /
    credit left the old balance in place while amount_currency was
    changed, which raised "The amount expressed in the secondary
    currency must be positive when account is debited...".
    """
    vals['balance'] = balance
    vals['debit'] = balance if balance > 0.0 else 0.0
    vals['credit'] = -balance if balance < 0.0 else 0.0
    vals['amount_currency'] = amount_currency


class AccountPayment(models.Model):
    _inherit = "account.payment"

    expense_type = fields.Many2one(
        'product.template',
        string="Expense Type",
        domain=[('can_be_expensed', '=', True)]
    )

    release_date = fields.Date(
        string="Release Date",
        copy=False,
        tracking=True,
        help="Date the payment (cash/check) was actually released to the payee.",
    )

    # ------------------------------------------------------------------
    # Fields stored on the posted payment
    # ------------------------------------------------------------------
    payment_tax_id = fields.Many2one(
        comodel_name='account.tax',
        string='Withholding Tax',
        copy=False,
    )
    payment_base_amount = fields.Monetary(
        string='Base Amount (Gross, before WHT)',
        currency_field='currency_id',
        copy=False,
    )
    payment_tax_amount = fields.Monetary(
        string='Withholding Tax Amount',
        currency_field='currency_id',
        copy=False,
    )
    
    wht_tax_ids = fields.Many2many(
        'account.tax',
        string="Withholding Tax",
        domain="[('type_tax_use', '=', 'sale')]"
    )
    wht_amount = fields.Monetary(
        string="WHT Amount",
        compute="_compute_wht_amount",
        store=True,
        currency_field='currency_id'
    )
    
    payment_net_amount = fields.Monetary(
        string="Net Amount",
        compute="_compute_payment_net_amount",
        store=True,
        currency_field='currency_id'
    )

    collection_receipt_id = fields.Many2one(
        'collection.receipt',
        string='Collection Receipt',
        copy=False,
        readonly=True
    )

    @api.depends('wht_tax_ids', 'reconciled_invoice_ids', 'memo')
    def _compute_wht_amount(self):
        for payment in self:
            if not payment.wht_tax_ids:
                payment.wht_amount = 0.0
                continue

            # Fetch linked invoices via reconciled records or memo matching
            invoices = payment.reconciled_invoice_ids
            if not invoices and payment.memo:
                invoices = self.env['account.move'].search([
                    ('name', '=', payment.memo),
                    ('move_type', 'in', ('out_invoice', 'in_invoice'))
                ], limit=1)

            if invoices:
                base_amount = sum(invoices.invoice_line_ids.mapped('price_subtotal'))
            else:
                base_amount = payment.amount

            taxes_res = payment.wht_tax_ids.compute_all(base_amount, currency=payment.currency_id)
            payment.wht_amount = sum(abs(t['amount']) for t in taxes_res['taxes'])

    @api.onchange('wht_tax_ids')
    def _onchange_wht_tax_ids(self):
        """ Automatically update the Net Payment Amount when WHT selection changes """
        if self.wht_tax_ids:
            invoices = self.reconciled_invoice_ids
            if not invoices and self.memo:
                invoices = self.env['account.move'].search([
                    ('name', '=', self.memo),
                    ('move_type', 'in', ('out_invoice', 'in_invoice'))
                ], limit=1)

            if invoices:
                total_invoice_amount = sum(invoices.mapped('amount_total'))
                self.amount = max(0.0, total_invoice_amount - self.wht_amount)
            

    # ADD THIS MISSING METHOD
    @api.depends('amount', 'wht_amount')
    def _compute_payment_net_amount(self):
        for payment in self:
            payment.payment_net_amount = (payment.amount or 0.0) - (payment.wht_amount or 0.0)

    def get_2307_details(self):
        """Build the BIR 2307 certificate line(s) directly from the
        withholding tax breakdown captured at payment-registration time.

        Previously this scanned every line in the payment's journal
        entry and summed gross/net across ALL of them (bank line, AR
        line, everything) whenever any line happened to have a
        tax_line_id, which inflated the totals. Reading the three
        fields stored on the payment itself is both simpler and
        correct regardless of how the underlying move is built.
        """
        self.ensure_one()

        if not self.payment_tax_id or not self.payment_tax_amount:
            return []

        tax = self.payment_tax_id
        net_amount = self.payment_base_amount - self.payment_tax_amount

        line = {
            'description': self.ref or (self.move_id.ref if self.move_id else ''),
            'atc': tax.name,
            'rate': abs(tax.amount),
            'month': self.date.month if self.date else False,
            'gross_amount': self.payment_base_amount,
            'tax_withheld': self.payment_tax_amount,
            'net_amount': net_amount,
            'tax_type': '',
            'payment_term': self.payment_type or '',
            'currency': self.currency_id.name,
            'conversion_rate': 1.0,
        }

        return [{
            'corporation': self.company_id,
            'lines': [line],
            'gross_amount': self.payment_base_amount,
            'tax_withheld': self.payment_tax_amount,
            'net_amount': net_amount,
        }]

    def get_2307_business_details(self):
        return self.get_2307_details()

    def action_print(self):
        self.ensure_one()

        if self.state != 'paid':
            raise UserError("Payment must be POSTED before generating BIR 2307.")

        return self.env.ref('itc_internal_dev.action_report_bir_2307').report_action(self)

    # ------------------------------------------------------------------
    # Bridge: WHT entered on the payment form (wht_tax_ids) -> the fields
    # _prepare_move_line_default_vals reads to build the CWT line.
    #
    # The register-payment wizard fills payment_tax_id / base / amount
    # directly. Payments created from the form only have wht_tax_ids and
    # wht_amount, so without this bridge no CWT line was ever posted.
    #
    # payment.amount is the NET cash received (the onchange sets it to
    # invoice total - WHT), so the gross that the AR line must clear is
    # amount + wht_amount.
    # ------------------------------------------------------------------
    def _sync_wht_to_payment_tax(self):
        # Payments created by the Register Payment wizard already carry the
        # exact base/WHT figures (incl. partial-payment proration), so don't
        # overwrite them with the memo-based wht_amount.
        if self.env.context.get('wht_from_register'):
            return
        for pay in self:
            if not pay.wht_tax_ids or not pay.wht_amount:
                continue
            if len(pay.wht_tax_ids) > 1:
                _logger.warning(
                    "Payment %s has %s withholding taxes; the CWT line will use "
                    "the account of %s for the combined amount.",
                    pay.name, len(pay.wht_tax_ids), pay.wht_tax_ids[0].name,
                )
            pay.write({
                'payment_tax_id': pay.wht_tax_ids[:1].id,
                'payment_tax_amount': pay.wht_amount,
                'payment_base_amount': pay.amount + pay.wht_amount,
            })

    # ------------------------------------------------------------------
    # Payment already has a journal entry (confirmed first, then reset to
    # draft to add WHT). Odoo's own sync only rewrites the bank and AR
    # lines from `amount`, so the CWT line never appears and AR is short
    # by the WHT. Rebuild every line from _prepare_move_line_default_vals
    # so the override above is applied.
    # ------------------------------------------------------------------
    def _rebuild_wht_move_lines(self):
        for pay in self:
            move = pay.move_id
            if not move or move.state != 'draft' or not pay.payment_tax_id:
                continue
            line_vals_list = pay._prepare_move_line_default_vals()
            move.with_context(skip_account_move_synchronization=True).write({
                'line_ids': [Command.delete(line.id) for line in move.line_ids]
                            + [Command.create(vals) for vals in line_vals_list],
            })

    # ------------------------------------------------------------------
    # Resetting to draft unreconciles the payment from the invoice, and
    # the rebuilt AR line is a new record anyway, so match it back to the
    # invoice (found by reconciliation or by memo = invoice number).
    # ------------------------------------------------------------------
    def _reconcile_wht_with_invoice(self):
        account_types = ('asset_receivable', 'liability_payable')
        for pay in self:
            if not pay.payment_tax_id or pay.move_id.state != 'posted':
                continue
            invoices = pay.reconciled_invoice_ids
            if not invoices and pay.memo:
                invoices = self.env['account.move'].search([
                    ('name', '=', pay.memo),
                    ('move_type', 'in', ('out_invoice', 'in_invoice')),
                    ('state', '=', 'posted'),
                    ('company_id', '=', pay.company_id.id),
                ], limit=1)
            if not invoices:
                continue
            lines = (pay.move_id.line_ids | invoices.line_ids).filtered(
                lambda l: l.account_id.account_type in account_types and not l.reconciled
            )
            for account in lines.account_id:
                to_reconcile = lines.filtered(lambda l: l.account_id == account)
                if len(to_reconcile) > 1:
                    to_reconcile.reconcile()

    # ------------------------------------------------------------------
    # Override: inject the withholding tax line into the journal entry.
    #
    # Deliberately does NOT call tax.compute_all() again here — it uses
    # the base/tax amounts already computed and stored by the wizard.
    # Recomputing from self.amount at this point would compute tax on
    # whatever self.amount currently holds; once the wizard sets amount
    # to the NET figure (see AccountPaymentRegister below), that would
    # mean computing tax on the net instead of the gross. Using the
    # stored values sidesteps that entirely.
    # ------------------------------------------------------------------
    def _prepare_move_line_default_vals(self, write_off_line_vals=None, force_balance=None, **kwargs):

        line_vals_list = super()._prepare_move_line_default_vals(
            write_off_line_vals=write_off_line_vals,
            force_balance=force_balance,
            **kwargs,
        )

        if not self.payment_tax_id or not self.payment_tax_amount or len(line_vals_list) < 2:
            return line_vals_list

        tax = self.payment_tax_id
        currency = self.currency_id

        base_amount = self.payment_base_amount
        tax_amount = self.payment_tax_amount  # stored positive at registration time
        net_amount = base_amount - tax_amount

        # line_vals_list[0] = liquidity line (bank/cash)
        # line_vals_list[1] = counterpart line (AR/AP outstanding)
        liquidity_line = line_vals_list[0]
        counterpart_line = line_vals_list[1]

        # Inbound (customer collection): bank/cash and CWT are debited, AR
        # credited. Outbound (vendor payment): the reverse. Taken from
        # payment_type rather than from the vals dicts, because Odoo 18
        # builds these lines with a 'balance' key and the old
        # bool(liquidity_line.get('debit')) check came out False for every
        # inbound payment.
        sign = 1 if self.payment_type == 'inbound' else -1

        # Amounts above are in the payment currency (amount_currency);
        # 'balance' must be in company currency. For PHP payments the two
        # are identical. The CWT line takes whatever balance closes the
        # entry, so foreign-currency rounding can't unbalance it.
        company_currency = self.company_id.currency_id

        def to_company(amount):
            if currency == company_currency:
                return amount
            return currency._convert(amount, company_currency, self.company_id, self.date or fields.Date.context_today(self))

        liquidity_amount_currency = sign * net_amount
        counterpart_amount_currency = -sign * base_amount
        tax_amount_currency = sign * tax_amount

        liquidity_balance = to_company(liquidity_amount_currency)
        counterpart_balance = to_company(counterpart_amount_currency)
        tax_balance = -(liquidity_balance + counterpart_balance)

        # Liquidity line: only the NET cash actually moves.
        # Counterpart line: still clears the FULL invoice amount.
        _set_line_amounts(liquidity_line, liquidity_amount_currency, liquidity_balance)
        _set_line_amounts(counterpart_line, counterpart_amount_currency, counterpart_balance)

        # Resolve the WHT account from the tax's own repartition lines
        repartition = tax.invoice_repartition_line_ids.filtered(
            lambda r: r.repartition_type == 'tax'
        )[:1]
        if not repartition:
            repartition = self.env['account.tax.repartition.line'].search([
                ('tax_id', '=', tax.id),
                ('repartition_type', '=', 'tax'),
                ('company_id', '=', self.company_id.id),
            ], limit=1)
        tax_account_id = (
            repartition.account_id.id if repartition and repartition.account_id
            else counterpart_line.get('account_id')
        )

        # The CWT line moves WITH the liquidity line (same side):
        # inbound -> debit (CWT is an asset), outbound -> credit (liability).
        tax_line_vals = {
            'name': tax.name,
            'account_id': tax_account_id,
            'currency_id': currency.id,
            'partner_id': self.partner_id.id if self.partner_id else False,
            'tax_base_amount': base_amount,
        }
        _set_line_amounts(tax_line_vals, tax_amount_currency, tax_balance)
        line_vals_list.append(tax_line_vals)
        # NOTE: deliberately not setting tax_line_id / tax_repartition_line_id /
        # tax_tag_ids here. Creditable WHT isn't a VAT-relevant tax for the
        # Philippine tax returns, so it shouldn't show up in the VAT report
        # grids. If some other report in your BIR suite expects to find this
        # by scanning journal items for tax_line_id, flag it and we'll add
        # those fields (with the correct tag mapping) too.

        return line_vals_list

    # ------------------------------------------------------------------
    # Log tax summary to chatter on post
    # ------------------------------------------------------------------
    def action_post(self):
        # Must run BEFORE super(): a new journal entry is generated during
        # posting, and _prepare_move_line_default_vals needs these fields.
        # If the payment already has a draft entry (reset to draft to add
        # WHT), its lines are rebuilt so the CWT line is included.
        wht_payments = self.filtered('wht_tax_ids')
        wht_payments._sync_wht_to_payment_tax()
        wht_payments._rebuild_wht_move_lines()

        res = super().action_post()

        wht_payments._reconcile_wht_with_invoice()

        # Log WHT summary
        for payment in self.filtered('payment_tax_id'):
            payment.message_post(body=_(
                "Withholding tax applied: %(tax)s — Base: %(base)s, WHT: %(tax_amt)s %(currency)s",
                tax=payment.payment_tax_id.name,
                base=payment.payment_base_amount,
                tax_amt=payment.payment_tax_amount,
                currency=payment.currency_id.name,
            ))

        # Auto-create Collection Receipt for customer inbound payments
        for pay in self.filtered(
            lambda p: p.partner_type == 'customer'
            and p.payment_type == 'inbound'
            and not p.collection_receipt_id
        ):
            try:
                cr = self.env['collection.receipt'].create({
                    'customer_id': pay.partner_id.id,
                    'amount': pay.amount,
                    'date': pay.date,
                    'payment_id': pay.id,
                    'state': 'confirmed',
                    'currency_id': pay.currency_id.id,
                    'company_id': pay.company_id.id,
                })
                cr.action_generate_pdf()
                pay.collection_receipt_id = cr.id
                _logger.info(
                    "Collection Receipt %s created for Payment %s",
                    cr.name, pay.name
                )
            except Exception as e:
                _logger.exception(
                    "Failed to create CR for Payment %s: %s", pay.name, e
                )

        return res
    
    def action_view_collection_receipt(self):
        """Open the linked Collection Receipt form."""
        self.ensure_one()
        if not self.collection_receipt_id:
            raise UserError(
                "No Collection Receipt linked to this payment yet."
            )
        return {
            'type': 'ir.actions.act_window',
            'name': 'Collection Receipt',
            'res_model': 'collection.receipt',
            'res_id': self.collection_receipt_id.id,
            'view_mode': 'form',
            'target': 'current',
        }


class AccountPaymentRegister(models.TransientModel):
    _inherit = 'account.payment.register'

    # ------------------------------------------------------------------
    # Fields
    # ------------------------------------------------------------------
    tax_id = fields.Many2one(
        comodel_name='account.tax',
        string='Withholding Tax',
        domain=[('type_tax_use', 'in', ['sale', 'purchase', 'none'])],
    )
    tax_amount = fields.Monetary(
        string='Withholding Tax Amount',
        compute='_compute_tax_breakdown',
        currency_field='currency_id',
    )
    base_amount = fields.Monetary(
        string='Base Amount (Gross)',
        compute='_compute_tax_breakdown',
        currency_field='currency_id',
    )
    total_with_tax = fields.Monetary(
        string='Net Amount (after WHT)',
        compute='_compute_tax_breakdown',
        currency_field='currency_id',
    )
    tax_breakdown_ids = fields.One2many(
        comodel_name='account.payment.register.tax.line',
        inverse_name='wizard_id',
        string='Tax Breakdown',
        compute='_compute_tax_breakdown',
    )

    # ------------------------------------------------------------------
    # Compute
    #
    # NOTE: this assumes your WHT tax records (e.g. WC160) are
    # configured with a NEGATIVE percentage amount, Odoo's standard
    # convention for withholding taxes, so that total_included comes
    # out lower than total_excluded. Worth confirming on one of your
    # actual WHT tax records before testing — if they're set up
    # positive instead, the sign in this compute needs to flip.
    #
    # Also worth flagging: as built, finance picks a WHT *tax record*
    # (an ATC code with a fixed rate) rather than typing a raw peso
    # amount. That's actually more correct for BIR purposes (2307
    # needs the ATC/rate anyway), but if she specifically wants to
    # type a number instead of picking a rate, say so and this can be
    # adjusted.
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # WHT is computed on the invoices' UNTAXED amount (VAT excluded), the
    # same basis as the payment form's wht_amount. Previously this ran
    # compute_all on wizard.amount, which includes VAT (e.g. 2% of 4,480
    # = 89.60 instead of 2% of 4,000 = 80.00).
    #
    # For a partial payment the untaxed base is prorated by
    # wizard.amount / invoice total.
    #
    #   base_amount    = wizard.amount (gross; what the AR line clears)
    #   tax_amount     = WHT
    #   total_with_tax = wizard.amount - WHT (net cash received)
    # ------------------------------------------------------------------
    @api.depends('amount', 'tax_id', 'currency_id', 'partner_id', 'line_ids')
    def _compute_tax_breakdown(self):
        TaxLine = self.env['account.payment.register.tax.line']
        for wizard in self:
            wizard.tax_breakdown_ids = TaxLine
            wizard.tax_amount = 0.0
            wizard.base_amount = wizard.amount
            wizard.total_with_tax = wizard.amount

            if not wizard.tax_id or not wizard.amount:
                continue

            currency = wizard.currency_id or self.env.company.currency_id

            invoices = wizard.line_ids.move_id.filtered(lambda m: m.is_invoice(include_receipts=True))
            inv_total = sum(invoices.mapped('amount_total'))
            inv_untaxed = sum(invoices.mapped('amount_untaxed'))
            if invoices and inv_total:
                ratio = min(wizard.amount / inv_total, 1.0)
                wht_base = currency.round(inv_untaxed * ratio)
            else:
                wht_base = wizard.amount

            taxes_res = wizard.tax_id.compute_all(
                price_unit=wht_base,
                currency=currency,
                quantity=1.0,
                partner=wizard.partner_id or None,
            )
            wht = currency.round(sum(abs(t['amount']) for t in taxes_res.get('taxes', [])))

            wizard.tax_amount = wht
            wizard.base_amount = wizard.amount
            wizard.total_with_tax = wizard.amount - wht

            lines = TaxLine
            for t in taxes_res.get('taxes', []):
                lines |= TaxLine.new({
                    'wizard_id': wizard.id,
                    'tax_id': t['id'],
                    'name': t['name'],
                    'base': t['base'],
                    'amount': abs(t['amount']),
                    'account_id': t.get('account_id') or False,
                })
            wizard.tax_breakdown_ids = lines

    # ------------------------------------------------------------------
    # Override: pass the WHT breakdown into the created payment, and set
    # the payment's own "Amount" to the NET figure so it matches what
    # actually moves through the bank line. wht_tax_ids is filled too so
    # the payment form shows the WHT exactly as if it had been entered
    # there.
    #
    # NOTE: only covers one payment per wizard run (single invoice, or
    # several invoices with "Group Payments" on). Paying several
    # invoices as separate payments goes through
    # _create_payment_vals_from_batch, which this doesn't touch.
    # ------------------------------------------------------------------
    def _create_payment_vals_from_wizard(self, batch_result):
        vals = super()._create_payment_vals_from_wizard(batch_result)
        if self.tax_id and self.tax_amount:
            vals['payment_tax_id'] = self.tax_id.id
            vals['payment_base_amount'] = self.base_amount
            vals['payment_tax_amount'] = self.tax_amount
            vals['amount'] = self.total_with_tax
            vals['wht_tax_ids'] = [Command.set(self.tax_id.ids)]
        return vals

    def _create_payments(self):
        return super(
            AccountPaymentRegister, self.with_context(wht_from_register=True)
        )._create_payments()


class AccountPaymentRegisterTaxLine(models.TransientModel):
    _name = 'account.payment.register.tax.line'
    _description = 'Payment Register Tax Breakdown Line'

    wizard_id = fields.Many2one(
        comodel_name='account.payment.register',
        required=True,
        ondelete='cascade',
    )
    tax_id = fields.Many2one('account.tax', string='Tax')
    name = fields.Char(string='Tax Name')
    base = fields.Monetary(string='Base Amount', currency_field='currency_id')
    amount = fields.Monetary(string='Tax Amount', currency_field='currency_id')
    account_id = fields.Many2one('account.account', string='Tax Account')
    currency_id = fields.Many2one(related='wizard_id.currency_id')