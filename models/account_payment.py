from odoo import api, fields, models, _
from odoo.exceptions import ValidationError, UserError


class AccountPayment(models.Model):
    _inherit = "account.payment"

    expense_type = fields.Many2one(
        'product.template',
        string="Expense Type",
        domain=[('can_be_expensed', '=', True)]
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

        liquidity_is_debit = bool(liquidity_line.get('debit', 0.0))
        sign = 1 if liquidity_is_debit else -1

        # Liquidity line: only the NET cash actually moves
        if liquidity_is_debit:
            liquidity_line['debit'] = net_amount
            liquidity_line['credit'] = 0.0
        else:
            liquidity_line['credit'] = net_amount
            liquidity_line['debit'] = 0.0
        liquidity_line['amount_currency'] = sign * net_amount

        # Counterpart line: still clears the FULL invoice amount
        if counterpart_line.get('debit', 0.0):
            counterpart_line['debit'] = base_amount
            counterpart_line['credit'] = 0.0
        else:
            counterpart_line['credit'] = base_amount
            counterpart_line['debit'] = 0.0
        counterpart_line['amount_currency'] = -sign * base_amount

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

        # FIX: the tax line must move WITH the liquidity line (same side),
        # not be branched on payment_type. Previously this was inverted:
        #   inbound  -> was credited (wrong; WHT receivable is an asset,
        #               should debit same as the bank line)
        #   outbound -> was debited (wrong; WHT payable is a liability,
        #               should credit same as the bank line)
        # which made the entry fail to balance.
        if liquidity_is_debit:
            tax_debit, tax_credit = tax_amount, 0.0
            tax_amount_currency = tax_amount
        else:
            tax_debit, tax_credit = 0.0, tax_amount
            tax_amount_currency = -tax_amount

        line_vals_list.append({
            'name': tax.name,
            'account_id': tax_account_id,
            'debit': tax_debit,
            'credit': tax_credit,
            'amount_currency': tax_amount_currency,
            'currency_id': currency.id,
            'partner_id': self.partner_id.id if self.partner_id else False,
            'tax_base_amount': base_amount,
        })
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
        res = super().action_post()
        for payment in self.filtered('payment_tax_id'):
            payment.message_post(body=_(
                "Withholding tax applied: %(tax)s — Base: %(base)s, WHT: %(tax_amt)s %(currency)s",
                tax=payment.payment_tax_id.name,
                base=payment.payment_base_amount,
                tax_amt=payment.payment_tax_amount,
                currency=payment.currency_id.name,
            ))
        return res


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
    @api.depends('amount', 'tax_id', 'currency_id', 'partner_id')
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
            taxes_res = wizard.tax_id.compute_all(
                price_unit=wizard.amount,
                currency=currency,
                quantity=1.0,
                partner=wizard.partner_id or None,
            )

            wizard.base_amount = taxes_res['total_excluded']
            wizard.total_with_tax = taxes_res['total_included']
            wizard.tax_amount = abs(wizard.total_with_tax - wizard.base_amount)

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
    # actually moves through the bank line.
    # ------------------------------------------------------------------
    def _create_payment_vals_from_wizard(self, batch_result):
        vals = super()._create_payment_vals_from_wizard(batch_result)
        if self.tax_id and self.tax_amount:
            vals['payment_tax_id'] = self.tax_id.id
            vals['payment_base_amount'] = self.base_amount
            vals['payment_tax_amount'] = self.tax_amount
            vals['amount'] = self.total_with_tax
        return vals


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