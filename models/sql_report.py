from odoo import models, fields, api
from odoo.exceptions import UserError, ValidationError
import io
import base64
import logging
import xlsxwriter
import json
from datetime import date, datetime
from markupsafe import Markup

_logger = logging.getLogger(__name__)

# -------------------------
# This is the report names used in the reporting module
# -------------------------
REPORT_NAMES = [
    ('sales_subsidiary_journal_rr9', 'Sales Subsidiary Journal RR9'),
    ('sales_journal', 'Sales Journal'),
    ('purchase_subsidiary_journal_rr9', 'Purchase Subsiday Journal RR9'),
    ('purchase_journal', 'Purchase Journal'),
    ('general_ledger', 'General Ledger'),
    ('general_ledger_rr9', 'General Ledger RR9'),
    ('general_journal', 'General Journal'),
    ('general_journal_rr9', 'General Journal RR9'),
    ('disbursement_journal', 'Disbursment Subsidiary Journal'),
    ('cash_receipt_journal', 'Cash Receipt Journal'),
    ('ar_history', 'Account Receivable History'),
    ('ap_by_transaction_date', 'Account Payable by Transaction Date'),
    ('aging_ar', 'Aging of Account Receivable'),
    ('aging_ap', 'Aging of Accout Payable'),
    ('ap_history', 'AP History'),
    ('cancelled_sales_invoice_summary', 'Cancelled Sales Invoice Summary Report'),
    ('cash_collection_summary', 'Cash Collection Summary Report'),
    ('check_collection_summary', 'Check Collection Summary Report'),
    ('deferred_vat_schedule', 'Deferred Vat Schedule'),
    ('depreciation_schedule', 'Depreciation Schedule'),
    ('disbursement_summary_by_date', 'Disbursement Summary Report by Date'),
    ('disbursement_summary_by_series', 'Disbursment Summary Report By Series No.'),
    ('disbursement_summary_detailed', 'Disbursement Summary Report Detailed'),
    ('disbursement_summary', 'Disbursement Summary Report'),
    ('lapsing_schedule', 'Lapsing Schedule'),
    ('or_by_customer', 'Official Receipt Report By Customer'),
    ('or_by_sales_rep', 'Official Receipt Report By Sales Representative'),
    ('or_by_series', 'Official Receipt Report By Series Number'),
    ('or_linked_invoice', 'Official Receipt Report Linked With service Invoice'),
    ('or_summary', 'OR Summary'),
    ('summary_ap', 'Summary of Accounts Payable'),
    ('summary_outstanding_ar', 'Summary of outstanding account receivable'),
    ('summary_outstanding_ap', 'Summary of Accounts outstanding paybale'),
    ('summary_paid_ap', 'Summary of Paid accounts payable'),
    ('summary_released_disbursement', 'Summary of Released Disbursement'),
    ('trial_balance', 'Trial Balance'),
    ('unpaid_sales_invoice_summary', 'Unpaid Sales Invoice Summary Report'),
    ('form_1604e', 'Form 1604E Schedule'),
    ('map_summary', 'MAP Summary  List'),
    ('qap_summary', 'QAP Summary List'),
    ('sawt', 'SAWT'),
    ('semestral_suppliers', 'Semestral List of Regular Supplier'),
    ('vat_summary_purchase', 'Vat Summary List - Purchase'),
    ('vat_summary_sales', 'Vat Summary List - Sales'),
    ('vat_summary_importation', 'Vat Summary List Importation'),
    ('audit_logs', 'Audit Logs'),
    ('form_1900', 'Form 1900'),
    ('secretary_cert', 'Secretary Cert'),
    ('inventory_book', 'Inventory Book'),
]

# Categories for various reports, can be expanded as needed.

REPORT_CATEGORIES = [
    ('annex', 'Annex'),
    ('books', 'Books'),
    ('other_reports', 'Other Reports'),
    ('tax_returns', 'Tax Returns Mandatory Attachments'),
    ('none', 'None'),
]

# -------------------------
# Corresponding SQL Queries Mapping for reports
# -------------------------
SQL_QUERIES = {
    'sales_subsidiary_journal_rr9': """
        SELECT 
            r.vat as "TIN",
            r."name" as "CUSTOMER CODE",
            r.complete_name as "CUSTOMER NAME",
            '' as "DESCRIPTION",
            a.name as "REFERENCE",
            a.amount_total as "AMOUNT",
            '' as "DISCOUNT",
            a.amount_tax as "VAT AMOUNT",
            abs(a.amount_total - a.amount_tax) as "NET SALES"
        FROM sale_order s
        INNER JOIN account_move a ON s.name = a.invoice_origin 
        LEFT JOIN res_partner r ON s.partner_id = r.id
        WHERE a.invoice_date BETWEEN %s AND %s;
    """,
    'sales_journal': """
        SELECT 		
            a.invoice_date AS "DATE",
            r.name AS "NAME OF CLIENT",
            r.contact_address_complete AS "ADDRESS",
            r.vat AS "TIN",
            a.name AS "PRIMARY",
            a.ref AS "SUPPLEMENTARY",
            '' AS "OTHERS",
            a.amount_total_signed AS "GROSS AMOUNT",
            '' AS "DISCOUNT AMOUNT",
            a.amount_total AS "SALES AMOUNT",
            CASE 
                WHEN a.x_invoice_type = 'consu' THEN a.amount_untaxed 
                ELSE 0 
            END AS "GOODS DOMESTIC SALES",
            CASE 
                WHEN a.x_invoice_type = 'service' THEN a.amount_untaxed 
                ELSE 0 
            END AS "SERVICE DOMESTIC SALES",
            a.amount_untaxed AS "TOTAL DOMESTIC SALES",
            case 
                when a.amount_tax > 0 then a.amount_untaxed 
                else 0
            end AS "PRIVATE",
            0 AS "GOVERNMENT",
            ABS(
                COALESCE(
                    CASE WHEN a.x_invoice_type = 'service' THEN a.amount_untaxed ELSE 0 END, 
                    0
                )
                -
                COALESCE(
                    CASE WHEN a.x_invoice_type = 'consu' THEN a.amount_untaxed ELSE 0 END, 
                    0
                )
            ) AS "TOTAL",
            case 
                when a.amount_tax > 0 then a.amount_untaxed 
                else 0
            end AS "VATABLE",
            case 
                when a.amount_tax = 0 then a.amount_untaxed 
                else 0
            end AS "ZERO RATED",
            '' AS "EXEMPT",
            ABS(a.amount_total - a.amount_tax) AS "TOTAL TAXABLE SALES",
            a.amount_tax AS "OUTPUT TAX 12%%",
            a.amount_total AS "SI/OR AMOUNT",
            0 AS "7%% STANDARD INPUT VAT",
            0 AS "BIR FORM VAT 2307 AMOUNT",
            0 AS "BIR FORM EWT 2307 AMOUNT"
        FROM account_move a
        LEFT JOIN res_partner r 
            ON a.partner_id = r.id
        WHERE a.state = 'posted' and a.invoice_date BETWEEN %s AND %s;
    """,
    'purchase_subsidiary_journal_rr9': """ 
        select 
            am."date" "DATE",
            rp.vat "TIN",
            rp."name" "SUPPLIER CODE",
            rp.complete_name "SUPPLIER NAME",
            '' "DESCRIPTION",
            po."name" "REFERENCE",
            am.amount_untaxed "AMOUNT",
            '' "DISCOUNT",
            am.amount_tax "VAT AMOUNT",
            abs(am.amount_untaxed - am.amount_tax) "NET PURCHASE"
        from purchase_order po
        inner join account_move am on po.name = am.invoice_origin
        left join res_partner rp on po.partner_id = rp.id
        where am."date" between %s and %s
        """,
        'purchase_journal': """
            SELECT
        a.invoice_date AS "TRANSACTION DATE",
        a.name AS "AP NUMBER",
        '' AS "DV NUMBER",
        '' AS "OTHERS",
        r.name AS "NAME OF PAYEE/SUPPLIER",
        r.contact_address_complete AS "ADDRESS",
        r.vat AS "TIN",
        a.invoice_date AS "REF DATE",
        a.name AS "PRIMARY",
        '' AS "SUPPLEMENTARY",
        '' AS "OTHERS",
        a.amount_total AS "GROSS AMOUNT",
        a.amount_tax AS "ACTUAL INPUT TAX 12%%",
        a.amount_untaxed AS "NET OF VAT",

        SUM(
            CASE 
                WHEN l.product_id IS NOT NULL 
                    AND l.account_id IS NOT NULL 
                    AND l.price_subtotal <= 1000000 THEN l.price_subtotal
                ELSE 0
            END
        ) AS "CAPITAL GOODS (AGGREGATE NOT EXCEEDING 1M)",

        SUM(
            CASE 
                WHEN l.product_id IS NOT NULL 
                    AND l.account_id IS NOT NULL 
                    AND l.price_subtotal > 1000000 THEN l.price_subtotal
                ELSE 0
            END
        ) AS "CAPITAL GOODS (AGGREGATE EXCEEDING 1M)",

        SUM(
            CASE
                WHEN l.product_id IS NOT NULL AND l.account_id IS NULL AND pt.type='cosu' THEN l.price_subtotal
                ELSE 0
            END
        ) AS "PURCHASE OTHER THAN CAPITAL GOODS",

        SUM(
            CASE
                WHEN l.product_id IS NOT NULL AND pt.type='service' AND a.partner_id IS NOT NULL THEN l.price_subtotal
                ELSE 0
            END
        ) AS "DOMESTIC PURCHASE OF SERVICES",

        SUM(
            CASE
                WHEN l.is_imported = TRUE THEN l.price_subtotal
                ELSE 0
            END
        ) AS "IMPORTATION PURCHASES",

        SUM(
            CASE
                WHEN t.amount = 0 THEN l.price_subtotal
                ELSE 0
            END
        ) AS "PURCHASE NOT QUALIFIED TO INPUT TAX",

        SUM(
            CASE
                WHEN l.product_id IS NULL THEN a.amount_total
                ELSE 0
            END
        ) AS "OTHERS",

        '' AS "ACCOUNT TITLE",
        '' AS "ATC",
        '' AS "RATE",
        '' AS "AMOUNT",
        '' AS "ALLOWED INPUT TAX",
        '' AS "DISALLOWED INPUT TAX",
        '' AS "DEFERRED INPUT TAX"

        FROM account_move a
        LEFT JOIN account_move_line l ON a.id = l.move_id
        left join product_product pp on l.product_id = pp.id 
        left join product_template pt on pp.product_tmpl_id = pt.id
        LEFT JOIN res_partner r ON a.partner_id = r.id
        LEFT JOIN account_move_line_account_tax_rel rel ON l.id = rel.account_move_line_id
        LEFT JOIN account_tax t ON rel.account_tax_id = t.id
        WHERE a.move_type in ('in_invoice', 'entry')
        and a.state = 'posted'
        AND a.invoice_date BETWEEN %s AND %s
        GROUP BY a.id, r.name, r.contact_address_complete, r.vat
        ORDER BY a.invoice_date;
    """,
    'disbursement_journal': """
        SELECT DISTINCT ON (he.id)
        am.create_date::date AS "RELEASED DATE",
        he.date::date AS "DATE",
        CONCAT('CV', LPAD(hes.id::text, 6, '0')) AS "VOUCHER NUMBER",
        p.default_code AS "TYPE",
        r.name AS "PAYEE / SUPPLIER",
        he.x_particulars AS "PARTICULARS",
        am.id AS "SI NO",
        ap.name AS "OR NO",
        '' AS "OTHER REFERENCES",
        he.total_amount AS "AMOUNT",
        CASE 
            WHEN at.name->>'en_US' ILIKE '%%0%%ZR%%' THEN he.total_amount
            ELSE 0 
        END AS "ZERO-RATED",
        CASE 
            WHEN at.name->>'en_US' ILIKE '%%0%%EXEMPT%%' THEN he.untaxed_amount_currency
            ELSE 0 
        END AS "EXEMPT / NON-VAT",
        CASE 
            WHEN at.name->>'en_US' ILIKE '%%12%%' THEN he.untaxed_amount_currency
            ELSE 0 
        END AS "VATABLE",
        he.tax_amount AS "INPUT TAX 12%%",
        he.total_amount AS "GROSS AMOUNT",
        CASE 
            WHEN at.name->>'en_US' ILIKE '%%12%%' THEN 'Y'
            ELSE 'N'
        END AS "INPUT TAX ALLOWED?",
        aa.name->>'en_US' AS "ACCOUNT TITLE"
        FROM hr_expense_sheet hes
        INNER JOIN hr_expense he ON hes.id = he.sheet_id
        INNER JOIN account_payment ap ON hes.journal_id = ap.journal_id
        LEFT JOIN account_move am ON ap.move_id = am.id
        LEFT JOIN account_move_line aml ON am.id = aml.move_id
        LEFT JOIN expense_tax et ON he.id = et.expense_id
        LEFT JOIN account_tax at ON et.tax_id = at.id
        LEFT JOIN product_product p ON he.product_id = p.id
        LEFT JOIN res_partner r ON he.vendor_id = r.id
        LEFT JOIN account_account aa ON he.account_id = aa.id
        ORDER BY he.id, hes.accounting_date, he.date, he.x_particulars;

    """,
    'ap_history': """ 
        SELECT
            am.invoice_date AS "AP DATE",
            am.name AS "AP ENTRY NUMBER",
            rp.name AS "NAME OF SUPPLIER",
            ap_line.account_name->>'en_US' AS "ACCOUNT TITLE",
            am.invoice_date AS "REFERENCE DATE",
            am.ref AS "SALES INVOICE",
            am.amount_total AS "BILLING",
            NULL AS "OTHERS",
            pt.name AS "TERMS",
            am.invoice_date_due AS "DUE DATE",
            am.amount_total AS "AMOUNT",
            ap_line.balance * -1 AS "ACCOUNTS PAYABLE",
            pay_move.date AS "CV RELEASE DATE",
            pay_move.name AS "CV NUMBER",
            COALESCE(pay_amount.applied_amount, 0) AS "CV AMOUNT",
            CASE
                WHEN am.state = 'cancel' THEN 'Cancelled'
                WHEN am.amount_residual = 0 THEN 'Paid'
                WHEN am.amount_residual < am.amount_total THEN 'Partially Paid'
                ELSE 'Open'
            END AS "STATUS",
            NULL AS "REMARKS"
        FROM account_move am
        LEFT JOIN res_partner rp ON rp.id = am.partner_id
        LEFT JOIN account_payment_term pt ON pt.id = am.invoice_payment_term_id
        LEFT JOIN (
            SELECT 
                aml.move_id,
                aml.balance,
                aa.name AS account_name
            FROM account_move_line aml
            JOIN account_account aa ON aa.id = aml.account_id
            WHERE aa.account_type = 'liability_payable'
        ) ap_line ON ap_line.move_id = am.id
        LEFT JOIN LATERAL (
            SELECT 
                SUM(pr.amount) AS applied_amount,
                pay_aml.move_id AS pay_move_id
            FROM account_move_line bill_aml
            JOIN account_partial_reconcile pr 
                ON pr.debit_move_id = bill_aml.id 
                OR pr.credit_move_id = bill_aml.id
            JOIN account_move_line pay_aml 
                ON pay_aml.id = pr.credit_move_id 
                OR pay_aml.id = pr.debit_move_id
            WHERE bill_aml.move_id = am.id
                AND pay_aml.move_id != am.id
            GROUP BY pay_aml.move_id
            LIMIT 1
        ) pay_amount ON TRUE
        LEFT JOIN account_move pay_move ON pay_move.id = pay_amount.pay_move_id
        WHERE am.move_type = 'in_invoice'
            AND am.state != 'draft'
        ORDER BY am.invoice_date DESC;

     """,
     'unpaid_sales_invoice_summary': """SELECT
            am.name AS "SALES INVOICE NUMBER",
            am.invoice_date AS "SALES INVOICE DATE",
            '' AS "BRANCH CODE",
            COALESCE(rc.name, '') AS "BRANCH NAME",
            rp.name AS "NAME OF CUSTOMER",
            sp.name AS "SALES REPRESENTATIVE",
            am.amount_total AS "INVOICE AMOUNT"
        FROM account_move am
        LEFT JOIN res_partner rp ON am.partner_id = rp.id
        LEFT JOIN res_users ru ON am.invoice_user_id = ru.id
        LEFT JOIN res_partner sp ON ru.partner_id = sp.id
        LEFT JOIN res_company rc ON am.company_id = rc.id
        WHERE am.move_type = 'out_invoice'
        AND am.state = 'posted'
        AND am.payment_state IN ('not_paid','partial')
        ORDER BY am.invoice_date DESC;
        """,
        'summary_paid_ap': """
        SELECT
            am.invoice_date AS "AP DATE",
            am.name AS "AP ENTRY NUMBER",
            rp.name AS "NAME OF SUPPLIER",
            a.name->> 'en_US' AS "ACCOUNT TITLE",
            am.invoice_date AS "REFERENCE DATE",
            am.invoice_origin AS "SALES INVOICE",
            am.amount_total AS "BILLING"
        FROM account_move am
        LEFT JOIN res_partner rp ON am.partner_id = rp.id
        LEFT JOIN account_move_line aal ON aal.move_id = am.id
        left join account_account a on aal.account_id = a.id
        WHERE am.move_type = 'in_invoice'
        AND am.state = 'posted'
        AND am.payment_state = 'paid'
        and a.name->> 'en_US' = 'Accounts Payable'
        and am.invoice_date between %s and %s
        ORDER BY am.invoice_date DESC;

        """,
        'summary_outstanding_ap': """SELECT
            am.invoice_date AS "AP DATE",
            am.name AS "AP ENTRY NUMBER",
            rp.name AS "NAME OF SUPPLIER",
            am.name AS "PARTICULARS",
            aa.name->> 'en_US' AS "ACCOUNT TITLE",
            am.invoice_date_due AS "DUE DATE",
            am.invoice_date AS "REFERENCE DATE",
            am.invoice_origin AS "SALES INVOICE",
            am.amount_total AS "BILLING",
            '' AS "OTHERS",
            am.amount_total AS "AMOUNT",
            COALESCE(ap.amount, 0) AS "CV AMOUNT",
            '' AS "EWT",
            (am.amount_total - COALESCE(ap.amount,0)) AS "PAYABLE AMOUNT",
            am.amount_total AS "TOTAL",
            am.payment_state AS "STATUS"
        FROM account_move am
        LEFT JOIN res_partner rp ON am.partner_id = rp.id
        LEFT JOIN account_move_line aml ON aml.move_id = am.id
        LEFT JOIN account_account aa ON aa.id = aml.account_id
        LEFT JOIN (
            SELECT move_id, SUM(amount) AS amount
            FROM account_payment
            WHERE state = 'posted'
            GROUP BY move_id
        ) ap ON ap.move_id = am.id
        WHERE am.move_type = 'in_invoice'
        AND am.state = 'posted'
        AND am.payment_state IN ('not_paid','partial')
        and aa.name->> 'en_US' = 'Accounts Payable'
        ORDER BY am.invoice_date DESC;
        """,
        'summary_outstanding_ar': """
        SELECT
            rp.name AS "NAME OF CUSTOMER",
            CURRENT_DATE AS "REPORT DATE",
            sp.name AS "SALES REPRESENTATIVE",
            am.invoice_date AS "SALES INVOICE DATE",
            am.name AS "INVOICE NUMBER",
            STRING_AGG(DISTINCT ap.name, ', ') AS "OR/CR NUMBER",
            '' AS "DELIVERY RECEIPT NUMBER",
            '' AS "OTHERS",
            am.amount_total AS "SALES INVOICE AMOUNT",
            COALESCE(SUM(aml_credit.amount_currency),0) AS "COLLECTED",
            (am.amount_total - COALESCE(SUM(aml_credit.amount_currency ),0)) AS "OUTSTANDING BALANCE"
        FROM account_move am
        LEFT JOIN res_partner rp ON am.partner_id = rp.id
        LEFT JOIN res_users ru ON am.invoice_user_id = ru.id
        LEFT JOIN res_partner sp ON ru.partner_id = sp.id
        LEFT JOIN account_partial_reconcile apr ON apr.debit_move_id IN (
            SELECT id FROM account_move_line WHERE move_id = am.id
        )
        LEFT JOIN account_move_line aml_credit ON aml_credit.id = apr.credit_move_id
        LEFT JOIN account_payment ap ON ap.id = aml_credit.payment_id AND ap.state='posted'
        WHERE am.move_type = 'out_invoice'
        AND am.state = 'posted'
        AND am.payment_state IN ('not_paid','partial')
        GROUP BY rp.name, sp.name, am.invoice_date, am.name, am.amount_total
        ORDER BY am.invoice_date DESC;
        """,
        'summary_ap': """
        SELECT
            am.invoice_date AS "AP DATE",
            am.name AS "AP ENTRY NUMBER",
            rp.name AS "NAME OF THE SUPPLIER",
            aa.name ->> 'en_US'AS "ACCOUNT TITLE",
            am.invoice_date AS "REFERENCE DATE",
            am.invoice_origin AS "SALES INVOICE",
            am.amount_total AS "BILLING",
            '' AS "OTHERS",
            COALESCE(am.invoice_payment_term_id, 0) AS "TERMS",
            am.invoice_date_due AS "DUE DATE",
            am.amount_total AS "AMOUNT",
            '' AS "EWT PAYABLE",
            (am.amount_total - COALESCE(ap.amount,0)) AS "ACCOUNTS PAYABLE",
            COALESCE(ap.paydate::text,'') AS "CV RELEASED DATE",
            COALESCE(ap.name,'') AS "CV NUMBER",
            COALESCE(ap.amount,0) AS "CV AMOUNT",
            am.payment_state  AS "STATUS"
        FROM account_move am
        LEFT JOIN res_partner rp ON am.partner_id = rp.id
        LEFT JOIN account_move_line aml ON aml.move_id = am.id
        LEFT JOIN account_account aa ON aa.id = aml.account_id
        LEFT JOIN (
            SELECT aml.move_id, ap.name, ap.date as paydate, SUM(aml.amount_currency ) AS amount
            FROM account_move_line aml	
            LEFT JOIN account_payment ap ON ap.id = aml.payment_id
            WHERE ap.state = 'posted'
            GROUP BY aml.move_id, ap.name, ap.date
        ) ap ON ap.move_id = am.id
        WHERE am.move_type = 'in_invoice'
        AND am.state = 'posted'
        and aa.name ->> 'en_US' = 'Accounts Payable'
        ORDER BY am.invoice_date DESC;
        """,
        'or_summary': """
        SELECT
            ap.date AS "OFFICIAL RECEIPT DATE",
            ap.name AS "OFFICIAL RECEIPT NO",
            rc.id AS "BRANCH CODE",
            rc.name AS "BRANCH NAME",
            sp.name AS "SALES REPRESENTATIVE",
            rp.name AS "NAME OF CUSTOMER",
            am.invoice_date AS "SERVICE INVOICE (SI) DATE"
        FROM account_payment ap
        LEFT JOIN res_partner rp ON ap.partner_id = rp.id
        LEFT JOIN res_users ru ON ap.partner_id = ru.id
        LEFT JOIN res_partner sp ON ru.partner_id = sp.id
        LEFT JOIN account_move am ON am.id = ap.move_id  -- payments applied to invoices
        LEFT JOIN res_company rc ON ap.company_id = rc.id
        WHERE ap.payment_type  = 'inbound'
        AND ap.state = 'paid'
        ORDER BY ap.date DESC;
        """,
        'lapsing_schedule': """
        SELECT
            aa.name AS "ASSET NAME",
            COALESCE(opening.opening_balance,0) AS "BEGINNING BALANCE",
            COALESCE(additions.added_amount,0) AS "ADDITIONS",
            (COALESCE(opening.opening_balance,0) + COALESCE(additions.added_amount,0)) AS "TOTAL",
            COALESCE(disposals.retired_amount,0) AS "DISPOSAL/RETIREMENT",
            ((COALESCE(opening.opening_balance,0) + COALESCE(additions.added_amount,0)) - COALESCE(disposals.retired_amount,0)) AS "NET TOTAL"
        FROM account_asset aa
        -- Opening balance
        LEFT JOIN (
            SELECT id as asset_id, SUM(original_value) AS opening_balance
            FROM account_asset
            WHERE state IN ('draft','open')
            GROUP BY id
        ) opening ON opening.asset_id = aa.id
        -- Additions
        LEFT JOIN (
            SELECT id as asset_id, SUM(original_value) AS added_amount
            FROM account_asset
            WHERE state = 'open'
            GROUP BY id
        ) additions ON additions.asset_id = aa.id
        -- Retirements / disposals
        LEFT JOIN (
            SELECT id as asset_id, SUM(original_value) AS retired_amount
            FROM account_asset
            WHERE state = 'close'
            GROUP BY id
        ) disposals ON disposals.asset_id = aa.id
        ORDER BY aa.name

        """,
        'disbursement_summary':"""
        SELECT
            ap.date AS "CV ENTRY DATE",
            ap.date AS "CV RELEASE DATE",
            ap.name AS "CV NUMBER",
            am.invoice_date AS "AP DATE",
            am.name AS "AP ENTRY NUMBER",
            rc.id AS "BRANCH CODE",
            rc.name AS "BRANCH NAME",
            rp.name AS "NAME OF PAYEE/SUPPLIER",
            am.invoice_origin AS "PARTICULARS",
            aa.name AS "ACCOUNT TITLE",
            am.amount_total AS "GROSS AMOUNT",
            '' AS "EWT AMOUNT",
            COALESCE(ap.amount,0) AS "CV AMOUNT",
            am.payment_state AS "STATUS"
        FROM account_payment ap
        LEFT JOIN account_move am ON am.id = ap.move_id  -- payments applied to invoices
        LEFT JOIN res_partner rp ON am.partner_id = rp.id
        LEFT JOIN res_company rc ON ap.company_id = rc.id
        LEFT JOIN account_move_line aml ON aml.move_id = am.id
        LEFT JOIN account_account aa ON aa.id = aml.account_id
        WHERE ap.payment_type = 'outbound'
        AND ap.state = 'posted'
        AND am.move_type = 'in_invoice'
        ORDER BY ap.date DESC;

        """,
        'check_collection_summary': """
        SELECT
            rp.name AS "NAME OF CUSTOMER",

            am.amount_total AS "CR/OR AMOUNT",
            am.invoice_date AS "CR/OR DATE",
            am.name AS "CR/OR NUMBER",

            ap.date AS "CHECK DATE",
            ap.check_number AS "CHECK NO",
            '' AS "BANK NAME",

            ap.amount AS "CHECK AMOUNT"

        FROM account_payment ap

        LEFT JOIN res_partner rp ON rp.id = ap.partner_id

        -- Link OR/CR (account_move) that sourced the payment
        LEFT JOIN account_move am ON am.id = ap.move_id

        WHERE ap.state = 'posted'
        AND ap.payment_method_line_id IS NOT NULL
        AND ap.payment_type = 'inbound'  -- collections only
        AND ap.check_number IS NOT NULL  -- only check payments
            AND ap.payment_method_line_id IN (
            SELECT id 
            FROM account_payment_method_line 
            WHERE name ILIKE '%%Checks%%'            
        )


        ORDER BY ap.date, rp.name; """,
        'cash_collection_summary': """ 
        SELECT
            rp.name AS "NAME OF CUSTOMER",

            am.amount_total AS "CR/OR AMOUNT",
            am.invoice_date AS "CR/OR DATE",
            am.name AS "CR/OR NUMBER",

            ap.date AS "CHECK DATE",
            ap.check_number AS "CHECK NO",
            '' AS "BANK NAME",

            ap.amount AS "CHECK AMOUNT"

        FROM account_payment ap

        LEFT JOIN res_partner rp ON rp.id = ap.partner_id

        -- Link OR/CR (account_move) that sourced the payment
        LEFT JOIN account_move am ON am.id = ap.move_id

        WHERE ap.state = 'posted'
        AND ap.payment_method_line_id IS NOT NULL
        AND ap.payment_type = 'inbound'  -- collections only
        AND ap.check_number is NULL  -- only check payments

        ORDER BY ap.date, rp.name;
        """,
        'general_journal': """
        SELECT
            aml.date AS DATE,
            am.name AS "JOURNAL BATCH ID",
            aml.name AS "DESCTIPTION",
            '' AS "ACCOUNT CODE",
            aa.name->>'en_US' AS "ACCOUNT TITLE",
            aml.ref AS "REF NUMBER",
            aml.debit AS "DEBIT",
            aml.credit AS "CREDIT"
        FROM account_move_line aml
        join account_account aa on aml.account_id = aa.id
        LEFT JOIN account_move am ON aml.move_id = am.id
        WHERE am.state = 'posted'
        ORDER BY aml.date, am.name, aml.id;
        """,
        'inventory_book': """
        SELECT
            sm.date::date AS "DATE",
            pt.name->> 'en_US' AS "PRODUCT NAME",
            sm.description_picking AS "DESCRIPTION",
            uom.name->>'en_US' AS "UNIT",

            -- Price per unit (from stock valuation layer)
            svl.unit_cost AS "PRICE PER UNIT",

            -- Amount = Qty * Unit Cost
            (svl.quantity * svl.unit_cost) AS "AMOUNT"

        FROM stock_move sm
        LEFT JOIN product_product pp ON sm.product_id = pp.id
        LEFT JOIN product_template pt ON pp.product_tmpl_id = pt.id
        LEFT JOIN uom_uom uom ON sm.product_uom = uom.id
        LEFT JOIN stock_valuation_layer svl ON svl.stock_move_id = sm.id
        where pt.name  is not null
        ORDER BY sm.date;
        """,
        'vat_summary_purchase': """
        SELECT
            am.name AS "SEQUENCE NUMBER",
            rp.vat AS "TAX PAYER IDENTIFICATION NUMBER",
            rp.name AS "REGISTERED NAME",
            -- Supplier Name: LAST, FIRST, MIDDLE (adjust fields if you have them in partner)
        ''
                AS "NAME OF SUPPLIER(LAST NAME, FIRST NAME, MIDDLE NAME)",
            rp.contact_address_complete AS "SUPPLIER ADDRESS",

            -- Amounts
            am.amount_untaxed + am.amount_tax AS "AMOUNT OF GROSS PURCHASE",
            0.0 AS "AMOUNT OF EXEMPT PURCHASE",       -- placeholder
            0.0 AS "AMOUNT OF ZERO RATED PURCHASE",   -- placeholder
            am.amount_untaxed AS "AMOUNT OF TAXABLE PURCHASE",

            -- Purchase types (you may need custom fields or tags to separate these)
            0.0 AS "AMOUNT OF PURCHASE OF SERVICES",           -- placeholder
            0.0 AS "AMOUNT OF PURCHASE OF CAPITAL GOODS",     -- placeholder
            0.0 AS "AMOUNT OF PURCHASE OF OTHER THAN CAPITAL GOODS", -- placeholder

            -- VAT
            am.amount_tax AS "AMOUNT OF INPUT TAX",

            -- Gross taxable purchase (for VAT computation)
            am.amount_untaxed AS "AMOUNT OF GROSS TAXABLE PURCHASE"

        FROM account_move am
        LEFT JOIN res_partner rp ON am.partner_id = rp.id
        WHERE am.move_type = 'in_invoice'
        AND am.state = 'posted'
        ORDER BY am.invoice_date, am.name;

        """,
        'vat_summary_sales': """
        SELECT
            TO_CHAR(am.invoice_date, 'MM/YYYY') AS "TAXABLE MONTH",
            rp.vat AS "TAX PAYER IDENTIFICATION NUMBER",
            rp.name AS "REGISTERED NAME",
            -- Customer Name: LAST, FIRST, MIDDLE (adjust fields if available)
        ''
                AS "NAME OF CUSTOMER",
            rp.contact_address_complete AS "CUSTOMER ADDRESS",

            -- Amounts
            am.amount_untaxed + am.amount_tax AS "AMOUNT OF GROSS SALES",
            0.0 AS "AMOUNT OF EXEMPT SALES",       -- placeholder, requires exemption logic
            0.0 AS "AMOUNT OF ZERO RATED SALES",   -- placeholder, requires zero-rated logic
            am.amount_untaxed AS "AMOUNT OF TAXABLE SALES",
            am.amount_tax AS "AMOUNT OF OUTPUT TAX",
            am.amount_untaxed AS "AMOUNT OF GROSS TAXABLE SALES"

        FROM account_move am
        LEFT JOIN res_partner rp ON am.partner_id = rp.id
        WHERE am.move_type = 'out_invoice'
        AND am.state = 'posted'
        ORDER BY am.invoice_date, am.name;

        """,
        'semestral_suppliers': """
        WITH supplier_invoices AS (
            SELECT
                p.id AS partner_id,
                p.vat AS tax_payer_id,
                p.branch_code AS branch_code,
                '' AS corporation,
                p.name AS last_name,          -- assuming single name field
                '' AS first_name,             -- if you have separate first name
                '' AS middle_name,            -- optional
                '' AS atc_code,
                l.debit AS amount_of_income_payment,
                t.amount AS tax_rate,
                l.tax_line_id AS tax_line_id,
                l.credit AS amount_of_tax_withheld,
                am.date AS invoice_date
            FROM account_move_line l
            JOIN account_move am ON l.move_id = am.id
            JOIN res_partner p ON am.partner_id = p.id
            LEFT JOIN account_tax t ON l.tax_line_id = t.id
            WHERE am.move_type = 'in_invoice'  -- supplier invoices
            AND am.state = 'posted'
        )
        SELECT
            ROW_NUMBER() OVER (ORDER BY invoice_date) AS sequence_number,
            tax_payer_id AS "TAX PAYER IDENTIFICATION NUMBER",
            branch_code AS "BRANCH CODE",
            corporation AS "CORPORATION",
            last_name AS "LAST NAME",
            first_name AS "FIRST NAME",
            middle_name AS "MIDDLE NAME",
            atc_code AS "ATC CODE",
            amount_of_income_payment AS "AMOUNT OF INCOME PAYMENT",
            tax_rate AS "TAX RATE",
            amount_of_tax_withheld AS "AMOUNT OF TAX WITHHELD"
        FROM supplier_invoices
        WHERE invoice_date >= date_trunc('year', CURRENT_DATE) 
        AND invoice_date < date_trunc('month', CURRENT_DATE) - INTERVAL '6 months'  -- last 6 months
        ORDER BY invoice_date;

        """,
        'map_summary': """
        WITH invoice_data AS (
            SELECT
                ROW_NUMBER() OVER (ORDER BY am.id) AS sequence_number,
                rp.vat AS taxpayer_identification_number,
                CASE WHEN rp.is_company THEN aml.debit ELSE 0 END AS corporation,
                CASE WHEN NOT rp.is_company THEN aml.debit ELSE 0 END AS individual,
                '' AS atc_code,
                atc.name->>'en_US' AS nature_of_payment,
                aml.debit AS amount_of_income_payment,
                '' AS tax_rate,
                aml.credit AS amount_of_tax_withheld
            FROM account_move_line aml
            JOIN account_move am ON aml.move_id = am.id
            JOIN res_partner rp ON am.partner_id = rp.id
            LEFT JOIN account_tax atc ON aml.tax_line_id = atc.id
            WHERE am.move_type IN ('out_invoice', 'out_refund') -- customer invoices
            AND am.state = 'posted'
        )
        SELECT
            sequence_number,
            taxpayer_identification_number,
            SUM(corporation) AS corporation,
            SUM(individual) AS individual,
            atc_code,
            nature_of_payment,
            SUM(amount_of_income_payment) AS amount_of_income_payment,
            tax_rate,
            SUM(amount_of_tax_withheld) AS amount_of_tax_withheld
        FROM invoice_data
        GROUP BY
            sequence_number,
            taxpayer_identification_number,
            atc_code,
            nature_of_payment,
            tax_rate
        ORDER BY sequence_number;

        """,
        'general_ledger': """
        SELECT
            am.date AS "DATE",
            am.name AS "REFERENCE",
            aml.name AS "DESCRIPTION",
            (aa.code_store ->> '1') || ' - ' || (aa.name ->> 'en_US') AS "ACCOUNT TITLE",
            aml.debit AS "DEBIT",
            aml.credit AS "CREDIT",
            SUM(aml.debit - aml.credit)
                OVER (
                    PARTITION BY aml.account_id
                    ORDER BY aml.id
                    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                ) AS "BALANCE"
        FROM account_move_line aml
        JOIN account_move am
            ON am.id = aml.move_id
        JOIN account_account aa
            ON aa.id = aml.account_id
        WHERE am.state = 'posted'
        ORDER BY aa.id, aml.id;
        """,
        'sawt': """
        WITH params AS (
            SELECT %s::date AS date_from, %s::date AS date_to
        )
        SELECT
            ROW_NUMBER() OVER (ORDER BY rp.name) AS "SEQ NO",
            rp.vat AS "TAXPAYER IDENTIFICATION NUMBER",
            rp.name AS "REGISTERED NAME",
            p.date_from AS "RETURN PERIOD FROM",
            p.date_to AS "RETURN PERIOD TO",
            UPPER(SUBSTRING(at.name->>'en_US' FROM 'W[CI][0-9]+')) AS "ATC CODE",
            '' AS "NATURE OF INCOME PAYMENT",
            SUM(aml.tax_base_amount) AS "AMOUNT",
            CONCAT(ABS(at.amount), '%%') AS "TAX RATE",
            SUM(aml.credit - aml.debit) AS "TAX WITHHELD"
        FROM account_move_line aml
        JOIN account_move am ON am.id = aml.move_id
        JOIN account_tax at ON aml.tax_line_id = at.id
        JOIN res_partner rp ON am.partner_id = rp.id
        CROSS JOIN params p
        WHERE am.move_type IN ('out_invoice', 'out_refund')
        AND am.state = 'posted'
        AND at.type_tax_use = 'sale'
        AND at.amount < 0
        AND am.invoice_date BETWEEN p.date_from AND p.date_to
        GROUP BY rp.name, rp.vat, at.name, at.amount, p.date_from, p.date_to
        ORDER BY rp.name;
    """,
    'qap_summary': """
        WITH params AS (
            SELECT %s::date AS date_from, %s::date AS date_to
        ),
        bounds AS (
            SELECT
                date_from, date_to,
                date_from AS m1_start,
                (date_from + INTERVAL '1 month' - INTERVAL '1 day')::date AS m1_end,
                (date_from + INTERVAL '1 month')::date AS m2_start,
                (date_from + INTERVAL '2 month' - INTERVAL '1 day')::date AS m2_end,
                (date_from + INTERVAL '2 month')::date AS m3_start,
                date_to AS m3_end
            FROM params
        )
        SELECT
            ROW_NUMBER() OVER (ORDER BY rp.name) AS "SEQ",
            rp.vat AS "TAXPAYER IDENTIFICATION NUMBER",
            CASE WHEN rp.is_company THEN rp.name ELSE '' END AS "CORPORATION",
            CASE WHEN NOT rp.is_company THEN rp.name ELSE '' END AS "INDIVIDUAL",
            UPPER(SUBSTRING(at.name->>'en_US' FROM 'W[CI][0-9]+')) AS "ATC CODE",
            '' AS "NATURE OF PAYMENT",
            SUM(CASE WHEN am.invoice_date BETWEEN b.m1_start AND b.m1_end THEN aml.tax_base_amount ELSE 0 END) AS "AMOUNT OF INCOME PAYMENT M1",
            CASE WHEN SUM(CASE WHEN am.invoice_date BETWEEN b.m1_start AND b.m1_end THEN aml.tax_base_amount ELSE 0 END) <> 0 THEN ABS(at.amount) END AS "TAX RATE M1",
            SUM(CASE WHEN am.invoice_date BETWEEN b.m1_start AND b.m1_end THEN ABS(aml.credit - aml.debit) ELSE 0 END) AS "TAX WITHHELD M1",
            SUM(CASE WHEN am.invoice_date BETWEEN b.m2_start AND b.m2_end THEN aml.tax_base_amount ELSE 0 END) AS "AMOUNT OF INCOME PAYMENT M2",
            CASE WHEN SUM(CASE WHEN am.invoice_date BETWEEN b.m2_start AND b.m2_end THEN aml.tax_base_amount ELSE 0 END) <> 0 THEN ABS(at.amount) END AS "TAX RATE M2",
            SUM(CASE WHEN am.invoice_date BETWEEN b.m2_start AND b.m2_end THEN ABS(aml.credit - aml.debit) ELSE 0 END) AS "TAX WITHHELD M2",
            SUM(CASE WHEN am.invoice_date BETWEEN b.m3_start AND b.m3_end THEN aml.tax_base_amount ELSE 0 END) AS "AMOUNT OF INCOME PAYMENT M3",
            CASE WHEN SUM(CASE WHEN am.invoice_date BETWEEN b.m3_start AND b.m3_end THEN aml.tax_base_amount ELSE 0 END) <> 0 THEN ABS(at.amount) END AS "TAX RATE M3",
            SUM(CASE WHEN am.invoice_date BETWEEN b.m3_start AND b.m3_end THEN ABS(aml.credit - aml.debit) ELSE 0 END) AS "TAX WITHHELD M3",
            SUM(aml.tax_base_amount) AS "TOTAL INCOME PAYMENT",
            SUM(ABS(aml.credit - aml.debit)) AS "TOTAL TAX WITHHELD"
        FROM account_move_line aml
        JOIN account_move am ON am.id = aml.move_id
        JOIN account_tax at ON aml.tax_line_id = at.id
        JOIN res_partner rp ON am.partner_id = rp.id
        CROSS JOIN bounds b
        WHERE am.move_type IN ('in_invoice', 'in_refund')
        AND am.state = 'posted'
        AND at.type_tax_use = 'purchase'
        AND at.amount < 0
        AND am.invoice_date BETWEEN b.date_from AND b.date_to
        GROUP BY rp.name, rp.vat, rp.is_company, at.name, at.amount
        ORDER BY rp.name;
    """,
}

""" Start of the class """
class SqlReport(models.Model):
    _name = 'custom.sql.report'
    _description = 'Custom SQL Report'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = "generated_on desc"

    name = fields.Selection(REPORT_NAMES, string="Report Name", required=True, tracking=True)
    report_category = fields.Selection(REPORT_CATEGORIES, string="Report Category", required=True, tracking=True)

    generated_on = fields.Datetime("Generated On", tracking=True, store=True)
    generated_by = fields.Many2one('res.users', string="Generated By", tracking=True, store=True)

    exported_on = fields.Datetime("Exported On", tracking=True, store=True)
    exported_by = fields.Many2one('res.users', string="Exported By", tracking=True, store=True)

    description = fields.Text("Description")

    from_date = fields.Date("From Date", required=True)
    to_date = fields.Date("To Date", required=True)

    sql_query = fields.Text("SQL Query")
    
    result_columns = fields.Text("Result Columns")  # JSON array
    filter_by_year = fields.Boolean(string="Filter By year", tracking=True, default=False ,help="Checking this will allow you to filter by year.")
    year = fields.Selection(
        [(str(y), str(y)) for y in range(2000, 2051)],
        string="Year",
        default=str(date.today().year),
        help="Select a year to auto-fill From Date and To Date"
    )
    _sql_constraints = [
        ('unique_report_name', 'unique(name)', 'Each report name must be unique!')
    ]

    filter_column = fields.Selection(
        selection=lambda self: self._get_dynamic_columns(),
        string="Filter Column"
    )

    filter_value = fields.Selection(
        selection=lambda self: self._get_unique_values(),
        string="Filter Value"
    )

    result_ids = fields.One2many('custom.sql.report.line', 'report_id', string="Results")
    def _get_dynamic_columns(self):
        """
        Return a list of (value, label) tuples even if no record is provided.
        Must NEVER call ensure_one(), because Odoo calls this as an empty recordset.
        """
        # If no record (self is empty), return empty selection
        if not self:
            return []

        # When editing a record, use its HTML or JSON data
        columns = []

        # Example: Extract column names from JSON data
        try:
            if self.result_ids:
                raw = [json.loads(r.data) for r in self.result_ids]
                if raw and len(raw) > 0:
                    for col in raw[0].keys():
                        columns.append((col, col))
        except Exception:
            pass

        return columns

    def _get_unique_values(self):
        """Return unique values of the selected filter column."""
        #self.ensure_one()


        unique_vals = set()

        for line in self.result_ids:
            if not line.data:
                continue

            try:
                row = json.loads(line.data)
            except Exception:
                continue

            # JSON row must be { "col": value, ... }
            if self.filter_column in row:
                val = row[self.filter_column]
                if val not in (None, ""):
                    unique_vals.add(str(val))

        # Convert to selection-friendly structure
        return [(v, v) for v in sorted(unique_vals)]



    @api.onchange('filter_column', 'filter_value')
    def _onchange_apply_filter(self):
        for line in self.result_ids:
            line.html_result = line.get_filtered_html(self.filter_column, self.filter_value)


    @api.onchange('year')
    def _onchange_year(self):
        if self.year:
            self.from_date = f'{self.year}-01-01'
            self.to_date = f'{self.year}-12-31'

    """ Generate Report Action"""
    def action_execute_query(self):
        self.ensure_one()

        if self.report_category not in ['books', 'annex', 'other_reports', 'tax_returns']:
            raise ValidationError("Please select a valid report category.")

        # Get the sql queries based on selected report
        sql = SQL_QUERIES.get(self.name)
        if not sql:
            raise ValidationError("Please select a valid report.")

        try:
            # Execute queries
            self.env.cr.execute(sql, (self.from_date, self.to_date))
            # Get columns based on description
            columns = [desc[0] for desc in self.env.cr.description]
            # fetch all rows based on the query
            rows = self.env.cr.fetchall()

            serializable_rows, row_html_parts = [], []
            numeric_totals = {col: 0 for col in columns}

            for row in rows:
                row_dict, cells = {}, []
                for col, val in zip(columns, row):
                    if isinstance(val, (datetime, date)):
                        val = val.isoformat()
                    elif isinstance(val, (int, float)):
                        numeric_totals[col] = numeric_totals.get(col, 0) + (val or 0)
                        formatted_val = f"{val:,.2f}" if isinstance(val, float) else f"{val:,}"
                        val = formatted_val
                    row_dict[col] = val
                    align = "right" if isinstance(val, str) and val.replace(",", "").replace(".", "").isdigit() else "left"
                    cells.append(f"<td style='width:200px; text-align:{align};'>{val or ''}</td>")
                serializable_rows.append(row_dict)
                row_html_parts.append(f"<tr>{''.join(cells)}</tr>")

            # --- Compute Totals ---
            total_cells = []
            for col in columns:
                total_val = numeric_totals.get(col)
                if isinstance(total_val, (int, float)) and total_val != 0:
                    formatted_total = f"{total_val:,.2f}"
                    total_cells.append(f"<td style='font-weight:bold; text-align:right;'>{formatted_total}</td>")
                elif col == columns[0]:
                    total_cells.append("<td style='font-weight:bold;'>TOTAL</td>")
                else:
                    total_cells.append("<td></td>")

            total_row_html = f"<tr style='background-color:#f0f0f0;'>{''.join(total_cells)}</tr>"

            # --- Build Final Table ---
            table_html = f"""
                <div  class="o_sql_report_result" style="
                    width: 100%;
                    max-width: 1000px;
                    max-height: 600px;
                    overflow-y: auto;
                    border: 1px solid #ccc;
                    border-radius: 6px;
                    position: relative;
                ">
                    <table class="table table-sm table-bordered" 
                        style="width:100%; border-collapse: collapse; table-layout: fixed;">
                        <thead style="position: sticky; top: 0; background-color: #f8f9fa; z-index: 2;">
                            <tr>
                                {"".join(f"<th style='width:200px; background-color:#f8f9fa; text-align:center; border:1px solid #dee2e6; position: sticky; top: 0;'>{col}</th>" for col in columns)}
                            </tr>
                        </thead>
                        <tbody>
                            {''.join(row_html_parts)}
                        </tbody>
                        <tfoot style="position: sticky; bottom: 0; background-color: #f0f0f0; z-index: 2; font-weight: bold;">
                            <tr>
                                {"".join(total_cells)}
                            </tr>
                        </tfoot>
                    </table>
                </div>
            """

            self.result_columns = json.dumps(columns)
            self.result_ids.unlink()
            self.env['custom.sql.report.line'].create({
                'report_id': self.id,
                'data': json.dumps(serializable_rows, ensure_ascii=False),
                'html_result': table_html,
            })

            self.generated_on = fields.Datetime.now()
            self.generated_by = self.env.user

        except Exception as e:
            raise UserError(f"Error executing query: {e}")


    def get_table_data(self):
        columns = json.loads(self.result_columns or "[]")
        rows = [json.loads(r.data) for r in self.result_ids]
        return columns, rows

    """ Export to Excel Action"""    

    def action_export_excel(self):
        self.ensure_one()
        if not self.result_ids:
            raise UserError("No data to export. Execute a query first.")

        columns = json.loads(self.result_columns or "[]")
        rows = [item for r in self.result_ids for item in json.loads(r.data or "[]")]

        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})
        worksheet = workbook.add_worksheet("Report")

        # === Formats ===
        bold_format = workbook.add_format({'bold': True})
        header_format = workbook.add_format({
            'bold': True, 'bg_color': '#ADD8E6',
            'border': 1, 'align': 'center',
            'valign': 'vcenter', 'text_wrap': True,
        })
        title_format = workbook.add_format({'bold': True, 'align': 'center'})
        small_format = workbook.add_format({'font_size': 9})
        text_format = workbook.add_format({'text_wrap': True, 'valign': 'top', 'border': 1, 'align': 'left'})
        number_format = workbook.add_format({'num_format': '#,##0.00', 'border': 1, 'align': 'right', 'valign': 'top'})
        total_format = workbook.add_format({'num_format': '#,##0.00', 'bold': True, 'border': 1, 'align': 'right', 'valign': 'top', 'bg_color': '#f0f0f0'})
        total_label_format = workbook.add_format({'bold': True, 'border': 1, 'align': 'left', 'valign': 'top', 'bg_color': '#f0f0f0'})

        company = self.env.company

        worksheet.write("A1", company.name or "", bold_format)

        worksheet.write(
            "A2",
            company.partner_id.contact_address_complete or "",
            small_format
        )

        worksheet.write(
            "A3",
            f"VAT REG TIN: {company.vat or ''}",
            small_format
        )
        worksheet.write("A5", dict(self._fields['name'].selection).get(self.name, self.name), title_format)
        worksheet.write("A6", f"Period Covered: {self.from_date.strftime('%m/%d/%Y')} - {self.to_date.strftime('%m/%d/%Y')}", small_format)

        worksheet.write("G1", f"No. Of Transactions: {len(rows)} ", small_format)
        self.exported_on = fields.Datetime.now()
        self.exported_by = self.env.user
        worksheet.write("G2", f"Date Processed: {self.exported_on.strftime('%m/%d/%Y')}", small_format)
        worksheet.write("G3", f"Processed by: {self.exported_by.name or ''}", small_format)

        worksheet.freeze_panes(7, 0)

        # === Table Header ===
        start_row = 6
        for col, col_name in enumerate(columns):
            worksheet.write(start_row, col, col_name, header_format)
            worksheet.set_column(col, col, len(col_name) + 10)

        # === Table Rows ===
        totals = {col: 0 for col in columns}
        for row_idx, row in enumerate(rows, start=start_row + 1):
            for col_idx, col_name in enumerate(columns):
                val = row.get(col_name, "")
                
                # Handle numeric values
                if isinstance(val, (int, float)):
                    totals[col_name] += val
                    if val == 0:
                        worksheet.write(row_idx, col_idx, "-", text_format)
                    else:
                        worksheet.write_number(row_idx, col_idx, val, number_format)
                else:
                    # Try parsing numeric strings (e.g., "1234" or "1,234.56")
                    try:
                        float_val = float(str(val).replace(",", ""))
                        totals[col_name] += float_val
                        if float_val == 0:
                            worksheet.write(row_idx, col_idx, "-", text_format)
                        else:
                            worksheet.write_number(row_idx, col_idx, float_val, number_format)
                    except Exception:
                        worksheet.write(row_idx, col_idx, val, text_format)


        # === Write Total Row ===
        total_row_idx = start_row + 1 + len(rows)
        for col_idx, col_name in enumerate(columns):
            total_val = totals.get(col_name)
            if col_idx == 0:
                worksheet.write(total_row_idx, col_idx, "TOTAL", total_label_format)
            elif isinstance(total_val, (int, float)) and total_val != 0:
                worksheet.write_number(total_row_idx, col_idx, total_val, total_format)
            else:
                worksheet.write(total_row_idx, col_idx, "", total_label_format)

        workbook.close()
        output.seek(0)

        # === Create Attachment ===
        file_data = base64.b64encode(output.read())
        filename = f"{self.name.replace(' ', '_')}.xlsx"

        attachment = self.env['ir.attachment'].create({
            'name': filename,
            'type': 'binary',
            'datas': file_data,
            'res_model': self._name,
            'res_id': self.id,
            'mimetype': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        })

       

        return {
            'type': 'ir.actions.act_url',
            'url': f"/web/content/{attachment.id}?download=true",
            'target': 'self',
        }

    """ Export to PDF Action"""
    def action_export_pdf(self):
        self.ensure_one()
        if not self.result_ids:
            raise UserError("No data to export. Execute a query first.")

        # Prepare HTML content
        html_content = self.result_ids[0].html_result or "<p>No data available</p>"

        # Optional: Wrap in basic HTML body for PDF rendering
        html = f"""
        <html>
            <head>
                <style>
                    table {{
                        width: 100%;
                        border-collapse: collapse;
                    }}
                    th, td {{
                        border: 1px solid #000;
                        padding: 4px;
                        text-align: left;
                    }}
                    th {{
                        background-color: #f8f9fa;
                    }}
                </style>
            </head>
            <body>
                <h2>{dict(self._fields['name'].selection).get(self.name, self.name)}</h2>
                <p>Period Covered: {self.from_date} - {self.to_date}</p>
                {html_content}
            </body>
        </html>
        """

        # Generate PDF using Odoo's built-in QWeb PDF engine
        pdf, _ = self.env['ir.actions.report']._get_pdf(
            docids=[], reportname='custom.sql.report.pdf', data={'html': html}
        )

        # Create attachment
        filename = f"{self.name.replace(' ', '_')}.pdf"
        attachment = self.env['ir.attachment'].create({
            'name': filename,
            'type': 'binary',
            'datas': base64.b64encode(pdf),
            'res_model': self._name,
            'res_id': self.id,
            'mimetype': 'application/pdf'
        })

        self.exported_on = fields.Datetime.now()
        self.exported_by = self.env.user

        return {
            'type': 'ir.actions.act_url',
            'url': f"/web/content/{attachment.id}?download=true",
            'target': 'self',
        }



class SqlReportLine(models.Model):
    _name = 'custom.sql.report.line'
    _description = 'Custom SQL Report Line'

    report_id = fields.Many2one('custom.sql.report', string="Report", ondelete="cascade")
    data = fields.Text("Row Data", help="Json data for the sql query")  # JSON string
    html_result = fields.Html("Details", help="Details fetched from data")  # HTML table


    # Create html table presenting the raw data

    def get_filtered_html(self, column, value):
        if not self.data:
            return ""

        import json
        rows = json.loads(self.data)

        if not rows:
            return ""

        # Determine headers:
        # If rows are dicts:
        if isinstance(rows[0], dict):
            headers = list(rows[0].keys())
        # If rows are lists (no column names)
        else:
            headers = [f"Column {i+1}" for i in range(len(rows[0]))]

        # Filter rows
        filtered = []
        for row in rows:
            if isinstance(row, dict):
                cell_value = row.get(column, "")
            else:
                # column is index
                try:
                    idx = int(column)
                    cell_value = row[idx]
                except:
                    continue

            # Convert both sides to string safely
            if str(value).lower() in str(cell_value).lower():
                filtered.append(row)


        # Build HTML table
        html = "<table class='table table-bordered'><thead><tr>"
        for h in headers:
            html += f"<th>{h}</th>"
        html += "</tr></thead><tbody>"

        for row in filtered:
            html += "<tr>"
            if isinstance(row, dict):
                for h in headers:
                    html += f"<td>{row.get(h, '')}</td>"
            else:
                for cell in row:
                    html += f"<td>{cell}</td>"
            html += "</tr>"

        html += "</tbody></table>"

        return html
