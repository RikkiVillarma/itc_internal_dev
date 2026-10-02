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
    ('withholding_tax_book', 'Withholding Tax Book'),
    ('form_1601e', 'Form 1601E'),
    ('form_1601eq', 'Form 1601EQ'),
    ('cash_flow_statement', 'Cash Flow Statement'),
    ('generic_tax_report', 'Generic Tax Report'),
    ('deferred_expense_report', 'Deferred Expense Report'),
    ('partner_ledger', 'Partner Ledger'),
]

# Categories for various reports, can be expanded as needed.

REPORT_CATEGORIES = [
    ('annex', 'Annex'),
    ('books', 'Books'),
    ('other_reports', 'Other Reports'),
    ('tax_returns', 'Tax Returns Mandatory Attachments'),
    ('none', 'None'),
]

_DISB_SUMMARY_SQL = """
        WITH cash_moves AS (
            SELECT
                aml.move_id,
                aml.credit AS disbursed_amount,
                aml.partner_id
            FROM account_move_line aml
            JOIN account_account aa ON aa.id = aml.account_id
            JOIN account_move am ON am.id = aml.move_id
            JOIN account_journal aj ON aj.id = am.journal_id
            WHERE aa.account_type = 'asset_cash'
              AND aml.credit > 0
              AND aj.type IN ('bank', 'cash')
              AND am.state = 'posted'
              AND am.date BETWEEN %s AND %s
        ),
        pay_bill AS (
            SELECT DISTINCT
                cm.move_id AS payment_move_id,
                bill_am.id AS bill_id
            FROM cash_moves cm
            JOIN account_move_line pay_aml
                ON pay_aml.move_id = cm.move_id
                AND pay_aml.full_reconcile_id IS NOT NULL
            JOIN account_account pay_aa
                ON pay_aa.id = pay_aml.account_id
                AND pay_aa.account_type = 'liability_payable'
            JOIN account_move_line bill_aml
                ON bill_aml.full_reconcile_id = pay_aml.full_reconcile_id
                AND bill_aml.id != pay_aml.id
            JOIN account_move bill_am ON bill_am.id = bill_aml.move_id
            WHERE bill_am.id != cm.move_id
              AND (bill_am.move_type = 'in_invoice' OR bill_am.expense_sheet_id IS NOT NULL)
        ),
        expense_line AS (
            SELECT DISTINCT ON (aml.move_id)
                aml.move_id,
                aa.name->>'en_US' AS account_title
            FROM account_move_line aml
            JOIN account_account aa ON aa.id = aml.account_id
            WHERE aml.tax_line_id IS NULL AND aml.product_id IS NOT NULL
            ORDER BY aml.move_id, aml.id
        ),
        payment_expense_line AS (
            -- Direct disbursements: first non-cash, non-tax line on the same move
            SELECT DISTINCT ON (aml.move_id)
                aml.move_id AS payment_move_id,
                aa.name->>'en_US' AS account_title,
                aml.name AS line_label
            FROM account_move_line aml
            JOIN account_account aa ON aa.id = aml.account_id
            WHERE aml.tax_line_id IS NULL
              AND aa.account_type NOT IN ('asset_cash', 'asset_bank', 'asset_current')
            ORDER BY aml.move_id, aml.id
        ),
        bill_amounts AS (
            SELECT
                bm.id AS move_id,
                ABS(bm.amount_untaxed_signed) AS untaxed,
                COALESCE(SUM(ABS(tl.balance)) FILTER (WHERE tx.amount > 0), 0) AS input_tax
            FROM account_move bm
            LEFT JOIN account_move_line tl
                ON tl.move_id = bm.id AND tl.tax_line_id IS NOT NULL
            LEFT JOIN account_tax tx ON tx.id = tl.tax_line_id
            WHERE bm.move_type = 'in_invoice'
            GROUP BY bm.id, bm.amount_untaxed_signed
        ),
        ewt AS (
            SELECT
                aml.move_id,
                SUM(ABS(aml.credit - aml.debit)) AS ewt_amount
            FROM account_move_line aml
            JOIN account_tax at ON at.id = aml.tax_line_id
            WHERE at.type_tax_use = 'purchase' AND at.amount < 0
            GROUP BY aml.move_id
        )
        SELECT
            am.create_date AS "CV ENTRY DATE",
            am.date AS "CV RELEASED DATE",
            am.name AS "CV NUMBER",
            bill_am.invoice_date AS "AP DATE",
            bill_am.name AS "AP ENTRY NUMBER",
            rc.id AS "BRANCH CODE",
            rc.name AS "BRANCH NAME",
            rp.name AS "NAME OF PAYEE/SUPPLIER",
            CASE
                WHEN hes.id IS NOT NULL THEN CONCAT('To record reimbursement for ', COALESCE(hes.name, el.account_title, ''))
                WHEN bill_am.id IS NOT NULL THEN CONCAT('To record payment for ', COALESCE(el.account_title, ''))
                ELSE COALESCE(NULLIF(am.ref, ''), pel.line_label, '')
            END AS "PARTICULARS",
            COALESCE(el.account_title, pel.account_title, '') AS "ACCOUNT TITLE",
            COALESCE(ba.untaxed + ba.input_tax,
                     cm.disbursed_amount + COALESCE(ewt_pay.ewt_amount, 0)) AS "GROSS AMOUNT",
            COALESCE(ewt_bill.ewt_amount, ewt_pay.ewt_amount, 0) AS "EWT AMOUNT",
            {extra_col}
            (COALESCE(ba.untaxed + ba.input_tax,
                      cm.disbursed_amount + COALESCE(ewt_pay.ewt_amount, 0))
                - COALESCE(ewt_bill.ewt_amount, ewt_pay.ewt_amount, 0)) AS "CV AMOUNT",
            'Released' AS "STATUS"
        FROM cash_moves cm
        JOIN account_move am ON am.id = cm.move_id
        LEFT JOIN res_company rc ON rc.id = am.company_id
        LEFT JOIN pay_bill pb ON pb.payment_move_id = cm.move_id
        LEFT JOIN account_move bill_am ON bill_am.id = pb.bill_id
        LEFT JOIN hr_expense_sheet hes ON hes.id = bill_am.expense_sheet_id
        LEFT JOIN res_partner rp ON rp.id = COALESCE(bill_am.partner_id, cm.partner_id)
        LEFT JOIN expense_line el ON el.move_id = bill_am.id
        LEFT JOIN payment_expense_line pel ON pel.payment_move_id = cm.move_id
        LEFT JOIN bill_amounts ba ON ba.move_id = bill_am.id
        LEFT JOIN ewt ewt_bill ON ewt_bill.move_id = bill_am.id
        LEFT JOIN ewt ewt_pay ON ewt_pay.move_id = cm.move_id
        ORDER BY {order_by};
"""

_DOC_REF_CTES = """
            cash_dir AS (
                SELECT l.move_id, BOOL_OR(l.credit > 0) AS is_outflow
                FROM account_move_line l
                JOIN account_account a ON a.id = l.account_id
                WHERE a.account_type = 'asset_cash'
                GROUP BY l.move_id
            ),
            pay_bill_docs AS (
                SELECT
                    pay_aml.move_id AS payment_move_id,
                    STRING_AGG(DISTINCT doc_am.name, ', ' ORDER BY doc_am.name) AS doc_names
                FROM account_move_line pay_aml
                JOIN account_move pay_am ON pay_am.id = pay_aml.move_id
                JOIN account_journal pay_aj ON pay_aj.id = pay_am.journal_id
                    AND pay_aj.type IN ('bank', 'cash')
                JOIN account_account pay_aa ON pay_aa.id = pay_aml.account_id
                    AND pay_aa.account_type = 'liability_payable'
                JOIN account_move_line doc_aml
                    ON doc_aml.full_reconcile_id = pay_aml.full_reconcile_id
                    AND doc_aml.id <> pay_aml.id
                JOIN account_move doc_am ON doc_am.id = doc_aml.move_id
                    AND doc_am.move_type IN ('in_invoice', 'in_refund')
                WHERE pay_aml.full_reconcile_id IS NOT NULL
                  AND doc_am.id <> pay_am.id
                GROUP BY pay_aml.move_id
            )
"""

_DOC_REF_EXPR = """
            CASE
                WHEN am.move_type IN ('in_invoice', 'in_refund') THEN 'AP# ' || am.name
                WHEN am.move_type IN ('out_invoice', 'out_refund') THEN 'SI# ' || am.name
                WHEN aj.type IN ('bank', 'cash') AND cd.move_id IS NOT NULL THEN
                    CASE WHEN cd.is_outflow THEN 'DV# ' || COALESCE(pb.doc_names, am.name)
                         ELSE 'CR# ' || am.name END
                ELSE am.name
            END
"""
# Tax returns a SAWT can be attached to; the code goes into every SAWT DAT record
SAWT_FORM_TYPES = [
    ('1702Q', '1702Q'),
    ('1701Q', '1701Q'),
    ('2550Q', '2550Q'),
    ('2551Q', '2551Q'),
    ('1702', '1702 (Annual)'),
    ('1701', '1701 (Annual)'),
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
        WHERE a.move_type = 'out_invoice'
            AND a.state = 'posted'
            AND a.invoice_date BETWEEN %s AND %s
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
            WITH params AS (
                SELECT %s::date AS date_from, %s::date AS date_to
            ),
            bill_lines AS (
                SELECT
                    aml.move_id,
                    ABS(aml.balance) AS net_amount,
                    aa.account_type,
                    aa.code_store->>am.company_id::text AS account_code,
                    aa.name->>'en_US' AS account_title,
                    pt.type AS product_type,
                    COALESCE(
                        aa.account_type IN ('asset_fixed', 'asset_non_current', 'non_current_assets')
                        OR aa.code_store->>am.company_id::text IN ('1101', '1102', '1103', '1104', '1105'),
                        FALSE
                    ) AS is_capital,
                    (rp.country_id = company_partner.country_id) AS is_domestic_service
                FROM account_move_line aml
                JOIN account_move am ON am.id = aml.move_id
                JOIN account_account aa ON aa.id = aml.account_id
                LEFT JOIN product_product pp ON pp.id = aml.product_id
                LEFT JOIN product_template pt ON pt.id = pp.product_tmpl_id
                LEFT JOIN res_partner rp ON rp.id = am.partner_id
                JOIN res_company company ON company.id = am.company_id
                JOIN res_partner company_partner ON company_partner.id = company.partner_id
                WHERE am.move_type = 'in_invoice'
                    AND am.state = 'posted'
                    AND aml.tax_line_id IS NULL
                    AND aml.product_id IS NOT NULL
            ),
            bill_classification AS (
                SELECT
                    move_id,
                    SUM(net_amount) FILTER (WHERE is_capital) AS capital_goods_total,
                    SUM(net_amount) FILTER (WHERE NOT is_capital AND product_type IS DISTINCT FROM 'service') AS other_goods,
                    SUM(net_amount) FILTER (WHERE NOT is_capital AND product_type = 'service' AND is_domestic_service) AS domestic_services,
                    STRING_AGG(DISTINCT account_title, ', ' ORDER BY account_title) AS account_title
                FROM bill_lines
                GROUP BY move_id
            ),
            expanded_withholding AS (
                SELECT
                    aml.move_id,
                    STRING_AGG(DISTINCT COALESCE(
                        NULLIF(SUBSTRING(tax.description->>'en_US' FROM 'W[CI]\\s*[0-9]+'), ''),
                        SUBSTRING(tax.name->>'en_US' FROM 'W[CI]\\s*[0-9]+')
                    ), ', ') FILTER (
                        WHERE tax.type_tax_use = 'purchase' AND tax.amount < 0
                    ) AS atc,
                    STRING_AGG(DISTINCT ABS(tax.amount)::text || '%%', ', ') FILTER (
                        WHERE tax.type_tax_use = 'purchase' AND tax.amount < 0
                    ) AS rate,
                    SUM(ABS(aml.balance)) FILTER (
                        WHERE tax.type_tax_use = 'purchase' AND tax.amount < 0
                    ) AS amount
                FROM account_move_line aml
                JOIN account_tax tax ON tax.id = aml.tax_line_id
                GROUP BY aml.move_id
            )
            SELECT
                purchase_order.date_order::date AS "TRANSACTION DATE",
                NULL AS "AP NUMBER",
                'DV# ' || bill.name AS "DV NUMBER",
                supplier.name AS "NAME OF PAYEE/SUPPLIER",
                supplier.contact_address_complete AS "ADDRESS",
                supplier.vat AS "TIN",
                bill.invoice_date AS "REF DATE",
                bill.ref AS "PRIMARY",
                purchase_order.name AS "SUPPLEMENTARY",
                bill.amount_untaxed + bill.amount_tax AS "GROSS AMOUNT",
                bill.amount_tax AS "ACTUAL INPUT TAX 12%%",
                ABS(bill.amount_untaxed_signed) AS "NET OF VAT",
                CASE WHEN COALESCE(classification.capital_goods_total, 0) > 0
                          AND classification.capital_goods_total <= 1000000
                     THEN classification.capital_goods_total ELSE 0 END AS "CAPITAL GOODS (AGGREGATE NOT EXCEEDING 1M)",
                CASE WHEN COALESCE(classification.capital_goods_total, 0) > 1000000
                     THEN classification.capital_goods_total ELSE 0 END AS "CAPITAL GOODS (AGGREGATE EXCEEDING 1M)",
                COALESCE(classification.other_goods, 0) AS "PURCHASE OTHER THAN CAPITAL GOODS",
                COALESCE(classification.domestic_services, 0) AS "DOMESTIC PURCHASE OF SERVICES",
                NULL AS "IMPORTATION PURCHASES",
                NULL AS "PURCHASE NOT QUALIFIED TO INPUT TAX",
                classification.account_title AS "ACCOUNT TITLE",
                withholding.atc AS "EWT ATC",
                withholding.rate AS "EWT RATE",
                COALESCE(withholding.amount, 0) AS "EWT AMOUNT",
                NULL AS "ALLOWED INPUT TAX",
                NULL AS "DISALLOWED INPUT TAX",
                NULL AS "DEFERRED INPUT TAX"
            FROM account_move bill
            JOIN res_partner supplier ON supplier.id = bill.partner_id
            JOIN purchase_order purchase_order
                ON purchase_order.name = bill.invoice_origin
                AND purchase_order.state IN ('purchase', 'done')
            LEFT JOIN bill_classification classification ON classification.move_id = bill.id
            LEFT JOIN expanded_withholding withholding ON withholding.move_id = bill.id
            CROSS JOIN params
            WHERE bill.move_type = 'in_invoice'
                AND bill.state = 'posted'
                AND bill.payment_state = 'paid'
                AND purchase_order.date_order BETWEEN params.date_from AND params.date_to
            ORDER BY purchase_order.date_order, purchase_order.name, bill.name;
        """,
    'cash_receipt_journal': """
        SELECT
            ap.date AS "DATE",
            ap.name AS "CR NUMBER",
            ap.state AS "PAYMENT STATE",
            rp.name AS "CUSTOMER",
            rp.vat AS "TIN",
            ap.memo AS "REFERENCE INVOICE",
            ap.amount AS "AMOUNT RECEIVED",
            aj.name->>'en_US' AS "JOURNAL"
        FROM account_payment ap
        JOIN account_journal aj ON aj.id = ap.journal_id
        LEFT JOIN res_partner rp ON rp.id = ap.partner_id
        WHERE ap.payment_type = 'inbound'
            AND ap.state = 'paid'
            AND aj.type = 'cash'
            AND ap.date BETWEEN %s AND %s
        ORDER BY ap.date, ap.name;
    """,
    'disbursement_journal': """
        WITH cash_moves AS (
            -- The actual cash/bank outflow lines
            SELECT
                aml.move_id,
                aml.credit AS disbursed_amount,
                aml.partner_id
            FROM account_move_line aml
            JOIN account_account aa ON aa.id = aml.account_id
            JOIN account_move am ON am.id = aml.move_id
            JOIN account_journal aj ON aj.id = am.journal_id
            WHERE aa.account_type = 'asset_cash'
              AND aml.credit > 0
              AND aj.type IN ('bank', 'cash')
              AND am.state = 'posted'
              AND am.date BETWEEN %s AND %s
        ),
        pay_bill AS (
            -- Disbursements that clear a vendor bill (or expense sheet) via full reconciliation
            SELECT DISTINCT
                cm.move_id AS payment_move_id,
                bill_am.id AS bill_id
            FROM cash_moves cm
            JOIN account_move_line pay_aml
                ON pay_aml.move_id = cm.move_id
                AND pay_aml.full_reconcile_id IS NOT NULL
            JOIN account_account pay_aa
                ON pay_aa.id = pay_aml.account_id
                AND pay_aa.account_type = 'liability_payable'
            JOIN account_move_line bill_aml
                ON bill_aml.full_reconcile_id = pay_aml.full_reconcile_id
                AND bill_aml.id != pay_aml.id
            JOIN account_move bill_am ON bill_am.id = bill_aml.move_id
            WHERE bill_am.id != cm.move_id
              AND (bill_am.move_type = 'in_invoice' OR bill_am.expense_sheet_id IS NOT NULL)
        ),
        ap_debit_moves AS (
            -- Bank moves that debit Accounts Payable (used to flag unapplied payments)
            SELECT DISTINCT aml.move_id
            FROM account_move_line aml
            JOIN account_account aa ON aa.id = aml.account_id
            WHERE aa.account_type = 'liability_payable'
              AND aml.debit > 0
        ),
        expense_line AS (
            SELECT DISTINCT ON (aml.move_id)
                aml.move_id,
                aa.name->>'en_US' AS account_title
            FROM account_move_line aml
            JOIN account_account aa ON aa.id = aml.account_id
            WHERE aml.tax_line_id IS NULL AND aml.product_id IS NOT NULL
            ORDER BY aml.move_id, aml.id
        ),
        payment_expense_line AS (
            -- Direct disbursements: expense line on the same move as the cash credit
            SELECT DISTINCT ON (aml.move_id)
                aml.move_id AS payment_move_id,
                aa.name->>'en_US' AS account_title,
                aml.name AS line_label,
                ABS(aml.debit - aml.credit) AS amount
            FROM account_move_line aml
            JOIN account_account aa ON aa.id = aml.account_id
            WHERE aa.account_type = 'expense'
            ORDER BY aml.move_id, aml.id
        ),
        payment_tax_line AS (
            SELECT
                aml.move_id AS payment_move_id,
                SUM(ABS(aml.debit - aml.credit)) AS tax_amount
            FROM account_move_line aml
            WHERE aml.tax_line_id IS NOT NULL
            GROUP BY aml.move_id
        ),
        bill_amounts AS (
            -- Company-currency (PHP) untaxed amount and input VAT per bill, before EWT
            SELECT
                bm.id AS move_id,
                ABS(bm.amount_untaxed_signed) AS untaxed,
                COALESCE(SUM(ABS(tl.balance)) FILTER (WHERE tx.amount > 0), 0) AS input_tax
            FROM account_move bm
            LEFT JOIN account_move_line tl
                ON tl.move_id = bm.id AND tl.tax_line_id IS NOT NULL
            LEFT JOIN account_tax tx ON tx.id = tl.tax_line_id
            WHERE bm.move_type = 'in_invoice'
            GROUP BY bm.id, bm.amount_untaxed_signed
        ),
        line_values AS (
            SELECT
                l.id, l.move_id, ABS(l.balance) AS line_amount,
                BOOL_OR(tag.name->>'en_US' ILIKE '%%46E%%') AS is_exempt,
                BOOL_OR(tag.name->>'en_US' ILIKE '%%46ZR%%') AS is_zero_rated,
                BOOL_OR(tag.name->>'en_US' ILIKE '%%42A%%') AS is_taxable
            FROM account_move_line l
            LEFT JOIN account_account_tag_account_move_line_rel tagrel ON tagrel.account_move_line_id = l.id
            LEFT JOIN account_account_tag tag ON tag.id = tagrel.account_account_tag_id
            WHERE l.product_id IS NOT NULL
            GROUP BY l.id, l.move_id, l.balance
        ),
        line_totals AS (
            SELECT
                move_id,
                SUM(CASE WHEN is_exempt THEN line_amount ELSE 0 END) AS exempt_amount,
                SUM(CASE WHEN is_zero_rated THEN line_amount ELSE 0 END) AS zero_rated_amount,
                SUM(CASE WHEN is_taxable THEN line_amount ELSE 0 END) AS vatable_amount
            FROM line_values
            GROUP BY move_id
        ),
        ewt AS (
            SELECT
                aml.move_id,
                MAX(ABS(at.amount)) / 100.0 AS ewt_rate,
                SUM(ABS(aml.credit - aml.debit)) AS ewt_amount
            FROM account_move_line aml
            JOIN account_tax at ON at.id = aml.tax_line_id
            WHERE at.type_tax_use = 'purchase' AND at.amount < 0
            GROUP BY aml.move_id
        )
        SELECT
            am.create_date AS "RELEASED DATE",
            COALESCE(bill_am.create_date, am.date) AS "DATE",
            COALESCE(bill_am.name, am.name) AS "NUMBER",
            CASE
                WHEN hes.id IS NOT NULL THEN 'EXPENSE REIMBURSEMENT'
                WHEN bill_am.id IS NOT NULL THEN 'BILL PAYMENT'
                WHEN adm.move_id IS NOT NULL THEN 'UNAPPLIED PAYMENT'
                ELSE 'DIRECT DISBURSEMENT'
            END AS "TYPE",
            CASE WHEN bill_am.id IS NOT NULL THEN am.name ELSE '' END AS "SECONDARY NUMBER",
            rp.name AS "PAYEE/SUPPLIER",
            CASE
                WHEN hes.id IS NOT NULL THEN CONCAT('To record reimbursement for ', COALESCE(hes.name, el.account_title, ''))
                WHEN bill_am.id IS NOT NULL THEN CONCAT('To record payment for ', COALESCE(el.account_title, ''))
                ELSE CONCAT('To record disbursement for ', COALESCE(pel.account_title, ''))
            END AS "PARTICULARS",
            CASE
                WHEN hes.id IS NOT NULL THEN CONCAT('EXP# ', COALESCE(hes.name, bill_am.name))
                WHEN bill_am.id IS NOT NULL THEN CONCAT('SAI# ', COALESCE(bill_am.ref, bill_am.name))
                ELSE COALESCE(NULLIF(am.ref, ''), pel.line_label, '')
            END AS "PRIMARY",
            '' AS "SUPPLEMENTARY",
            '' AS "OTHER REFERENCES",
            cm.disbursed_amount AS "AMOUNT",
            COALESCE(lt.zero_rated_amount, 0) AS "ZERO-RATED",
            COALESCE(lt.exempt_amount, 0) AS "EXEMPT/NON-VAT",
            COALESCE(lt.vatable_amount, ba.untaxed, pel.amount) AS "VATABLE",
            COALESCE(ba.input_tax, pt.tax_amount, 0) AS "INPUT TAX 12%%",
            COALESCE(ba.untaxed + ba.input_tax, cm.disbursed_amount) AS "GROSS AMOUNT",
            CASE WHEN COALESCE(ba.input_tax, pt.tax_amount, 0) > 0 THEN 'Y' ELSE 'N' END AS "INPUT TAX ALLOWED?",
            COALESCE(lt.vatable_amount, ba.untaxed, pel.amount) AS "TAX BASE",
            COALESCE(ewt.ewt_rate, 0) AS "RATE",
            COALESCE(ewt.ewt_amount, 0) AS "EWT AMOUNT",
            'N' AS "EWT ABSORBED BY COMPANY?",
            (COALESCE(ba.untaxed + ba.input_tax, cm.disbursed_amount)
                - COALESCE(ewt.ewt_amount, 0)) AS "CV AMOUNT (CREDIT)",
            COALESCE(el.account_title, pel.account_title, '') AS "ACCOUNT TITLE"
        FROM cash_moves cm
        JOIN account_move am ON am.id = cm.move_id
        LEFT JOIN pay_bill pb ON pb.payment_move_id = cm.move_id
        LEFT JOIN account_move bill_am ON bill_am.id = pb.bill_id
        LEFT JOIN hr_expense_sheet hes ON hes.id = bill_am.expense_sheet_id
        LEFT JOIN ap_debit_moves adm ON adm.move_id = cm.move_id
        LEFT JOIN res_partner rp ON rp.id = COALESCE(bill_am.partner_id, cm.partner_id)
        LEFT JOIN expense_line el ON el.move_id = bill_am.id
        LEFT JOIN payment_expense_line pel ON pel.payment_move_id = cm.move_id
        LEFT JOIN payment_tax_line pt ON pt.payment_move_id = cm.move_id
        LEFT JOIN bill_amounts ba ON ba.move_id = bill_am.id
        LEFT JOIN line_totals lt ON lt.move_id = bill_am.id
        LEFT JOIN ewt ON ewt.move_id = bill_am.id
        ORDER BY am.date, am.name;
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
    'disbursement_summary': _DISB_SUMMARY_SQL.format(
        extra_col='', order_by='am.date DESC, am.name DESC'),
    'disbursement_summary_by_date': _DISB_SUMMARY_SQL.format(
        extra_col="'N' AS \"ABSORBED EWT?\",", order_by='am.date ASC, am.name ASC'),
    'disbursement_summary_by_series': _DISB_SUMMARY_SQL.format(
        extra_col="'N' AS \"ABSORBED EWT?\",", order_by='am.name ASC'),
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
            WITH {doc_ctes}
            SELECT
                am.date AS "DATE",
                am.name AS "JOURNAL BATCH ID",
                COALESCE(NULLIF(aml.name, ''), am.ref, '') AS "DESCRIPTION",
                (aa.code_store ->> am.company_id::text) AS "ACCOUNT CODE",
                (aa.name ->> 'en_US') AS "ACCOUNT TITLE",
                {doc_expr} AS "REF NUMBER",
                aml.debit AS "DEBIT",
                aml.credit AS "CREDIT"
            FROM account_move_line aml
            JOIN account_move am ON am.id = aml.move_id
            JOIN account_journal aj ON aj.id = am.journal_id
            JOIN account_account aa ON aa.id = aml.account_id
            LEFT JOIN cash_dir cd ON cd.move_id = am.id
            LEFT JOIN pay_bill_docs pb ON pb.payment_move_id = am.id
            WHERE am.state = 'posted'
              AND am.date BETWEEN %s AND %s
            ORDER BY am.date, am.name, aml.id;
        """.format(doc_ctes=_DOC_REF_CTES, doc_expr=_DOC_REF_EXPR),
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
            WITH params AS (
                SELECT %s::date AS date_from, %s::date AS date_to
            ),
            hdr AS (
                SELECT
                    am.id AS move_id,
                    am.partner_id,
                    COALESCE(NULLIF(TRIM(am.x_partner_tin), ''), '__PARTNER_' || am.partner_id::text) AS tin_key,
                    am.x_partner_tin,
                    rp.name,
                    rp.contact_address_complete,
                    am.amount_untaxed + am.amount_tax AS gross_purchase,
                    am.amount_tax AS input_tax,
                    am.amount_untaxed AS taxable_purchase
                FROM purchase_order po
                INNER JOIN account_move am 
                    ON po.name = am.invoice_origin
                    AND am.move_type = 'in_invoice'
                    AND am.state = 'posted'
                    AND am.payment_state = 'paid'
                JOIN res_partner rp ON rp.id = am.partner_id
                CROSS JOIN params p
                WHERE po.state IN ('purchase', 'done')
                    AND po.date_order BETWEEN p.date_from AND p.date_to
            ),
            line_values AS (
                SELECT
                    l.id,
                    l.move_id,
                    l.price_subtotal,
                    pt.type AS product_type,
                    aa.code_store->>'1' AS account_code,
                    BOOL_OR(tag.name->>'en_US' ILIKE '%%46E%%') AS is_exempt,
                    BOOL_OR(tag.name->>'en_US' ILIKE '%%46ZR%%') AS is_zero_rated,
                    BOOL_OR(tag.name->>'en_US' ILIKE '%%42A%%') AS is_taxable
                FROM account_move_line l
                LEFT JOIN product_product pp ON pp.id = l.product_id
                LEFT JOIN product_template pt ON pt.id = pp.product_tmpl_id
                LEFT JOIN account_account aa ON aa.id = l.account_id
                LEFT JOIN account_account_tag_account_move_line_rel tagrel ON tagrel.account_move_line_id = l.id
                LEFT JOIN account_account_tag tag ON tag.id = tagrel.account_account_tag_id
                WHERE l.product_id IS NOT NULL
                GROUP BY l.id, l.move_id, l.price_subtotal, pt.type, aa.code_store
            ),
            line_totals AS (
                SELECT
                    move_id,
                    SUM(CASE WHEN is_exempt THEN price_subtotal ELSE 0 END) AS exempt_purchase,
                    SUM(CASE WHEN is_zero_rated THEN price_subtotal ELSE 0 END) AS zero_rated_purchase,
                    SUM(CASE WHEN is_taxable THEN price_subtotal ELSE 0 END) AS taxable_purchase,
                    SUM(CASE WHEN is_taxable AND product_type = 'service' THEN price_subtotal ELSE 0 END) AS service_purchase,
                    SUM(CASE WHEN is_taxable AND account_code IN ('1101','1102','1103','1104','1105') THEN price_subtotal ELSE 0 END) AS capital_goods,
                    SUM(CASE WHEN is_taxable AND product_type != 'service' AND account_code NOT IN ('1101','1102','1103','1104','1105') THEN price_subtotal ELSE 0 END) AS other_goods
                FROM line_values
                GROUP BY move_id
            )
            SELECT
                ROW_NUMBER() OVER (ORDER BY MAX(hdr.name)) AS "SEQ NO",
                MAX(hdr.x_partner_tin) AS "TAX PAYER IDENTIFICATION NUMBER",
                MAX(hdr.name) AS "REGISTERED NAME",
                '' AS "NAME OF SUPPLIER (LAST NAME, FIRST NAME, MIDDLE NAME)",
                MAX(hdr.contact_address_complete) AS "SUPPLIER ADDRESS",
                SUM(hdr.gross_purchase) AS "AMOUNT OF GROSS PURCHASE",
                SUM(COALESCE(lt.exempt_purchase, 0)) AS "AMOUNT OF EXEMPT PURCHASE",
                SUM(COALESCE(lt.zero_rated_purchase, 0)) AS "AMOUNT OF ZERO-RATED PURCHASE",
                SUM(COALESCE(lt.taxable_purchase, 0)) AS "AMOUNT OF TAXABLE PURCHASE",
                SUM(COALESCE(lt.service_purchase, 0)) AS "AMOUNT OF PURCHASE OF SERVICES",
                SUM(COALESCE(lt.capital_goods, 0)) AS "AMOUNT OF PURCHASE OF CAPITAL GOODS",
                SUM(COALESCE(lt.other_goods, 0)) AS "AMOUNT OF PURCHASE OF GOODS OTHER THAN CAPITAL GOODS",
                SUM(hdr.input_tax) AS "AMOUNT OF INPUT TAX",
                SUM(hdr.taxable_purchase) AS "AMOUNT OF GROSS TAXABLE PURCHASE"
            FROM hdr
            LEFT JOIN line_totals lt ON lt.move_id = hdr.move_id
            GROUP BY hdr.tin_key
            ORDER BY MAX(hdr.name);
        """,
        'vat_summary_sales': """
            WITH params AS (
                SELECT %s::date AS date_from, %s::date AS date_to
            ),
            hdr AS (
                SELECT
                    am.id,
                    am.partner_id,
                    TO_CHAR(am.invoice_date, 'MM/YYYY') AS taxable_month,
                    COALESCE(NULLIF(TRIM(am.x_partner_tin), ''), '__PARTNER_' || am.partner_id::text) AS tin_key,
                    am.x_partner_tin,
                    rp.name,
                    rp.contact_address_complete,
                    am.amount_untaxed + am.amount_tax AS gross_sales,
                    am.amount_tax AS output_tax,
                    am.amount_untaxed AS taxable_sales
                FROM account_move am
                JOIN res_partner rp ON rp.id = am.partner_id
                CROSS JOIN params p
                WHERE am.move_type = 'out_invoice'
                    AND am.state = 'posted'
                    AND am.invoice_date BETWEEN p.date_from AND p.date_to
            ),
            line_values AS (
                SELECT
                    l.id,
                    l.move_id,
                    l.price_subtotal,
                    BOOL_OR(tag.name->>'en_US' ILIKE '%%34A%%') AS is_exempt,
                    BOOL_OR(tag.name->>'en_US' ILIKE '%%33A%%') AS is_zero_rated,
                    BOOL_OR(tag.name->>'en_US' ILIKE '%%31A%%') AS is_private,
                    BOOL_OR(tag.name->>'en_US' ILIKE '%%32A%%') AS is_government
                FROM account_move_line l
                LEFT JOIN account_account_tag_account_move_line_rel tagrel ON tagrel.account_move_line_id = l.id
                LEFT JOIN account_account_tag tag ON tag.id = tagrel.account_account_tag_id
                WHERE l.product_id IS NOT NULL
                GROUP BY l.id, l.move_id, l.price_subtotal
            ),
            line_totals AS (
                SELECT
                    move_id,
                    SUM(CASE WHEN is_exempt THEN price_subtotal ELSE 0 END) AS exempt_sales,
                    SUM(CASE WHEN is_zero_rated THEN price_subtotal ELSE 0 END) AS zero_rated_sales,
                    SUM(CASE WHEN is_private THEN price_subtotal ELSE 0 END) AS private_sales,
                    SUM(CASE WHEN is_government THEN price_subtotal ELSE 0 END) AS government_sales
                FROM line_values
                GROUP BY move_id
            )
            SELECT
                hdr.taxable_month AS "TAXABLE MONTH",
                MAX(hdr.x_partner_tin) AS "TAX PAYER IDENTIFICATION NUMBER",
                MAX(hdr.name) AS "REGISTERED NAME",
                '' AS "NAME OF CUSTOMER",
                MAX(hdr.contact_address_complete) AS "CUSTOMER ADDRESS",
                SUM(hdr.gross_sales) AS "AMOUNT OF GROSS SALES",
                SUM(COALESCE(lt.exempt_sales, 0)) AS "AMOUNT OF EXEMPT SALES",
                SUM(COALESCE(lt.zero_rated_sales, 0)) AS "AMOUNT OF ZERO RATED SALES",
                SUM(COALESCE(lt.private_sales, 0)) AS "AMOUNT OF TAXABLE SALES - PRIVATE",
                SUM(COALESCE(lt.government_sales, 0)) AS "AMOUNT OF TAXABLE SALES - GOVERNMENT",
                SUM(hdr.output_tax) AS "AMOUNT OF OUTPUT TAX",
                SUM(hdr.taxable_sales) AS "AMOUNT OF GROSS TAXABLE SALES"
            FROM hdr
            LEFT JOIN line_totals lt ON lt.move_id = hdr.id
            GROUP BY hdr.tin_key, hdr.taxable_month
            ORDER BY hdr.taxable_month, MAX(hdr.name);
        """,
        'semestral_suppliers': """
            WITH params AS (
                SELECT %s::date AS date_from, %s::date AS date_to
            )
            SELECT
                ROW_NUMBER() OVER (ORDER BY rp.name) AS "SEQ NO",
                rp.vat AS "TAX PAYER IDENTIFICATION NUMBER",
                '00000' AS "BRANCH CODE",
                CASE WHEN rp.is_company THEN rp.name ELSE '' END AS "CORPORATION",
                CASE WHEN NOT rp.is_company THEN rp.name ELSE '' END AS "LAST NAME",
                '' AS "FIRST NAME",
                '' AS "MIDDLE NAME",
                UPPER(
                    REGEXP_REPLACE(
                        COALESCE(
                            NULLIF(SUBSTRING(at.description->>'en_US' FROM 'W[CI]\\s*[0-9]+'), ''),
                            SUBSTRING(at.name->>'en_US' FROM 'W[CI]\\s*[0-9]+')
                        ),
                        '\\s+', '', 'g'
                    )
                ) AS "ATC CODE",
                SUM(aml.tax_base_amount) AS "AMOUNT OF INCOME PAYMENT",
                CONCAT(ABS(at.amount)::numeric(10,2), '%%') AS "TAX RATE",
                SUM(ABS(aml.credit - aml.debit)) AS "AMOUNT OF TAX WITHHELD"
            FROM account_move_line aml
            JOIN account_move am ON am.id = aml.move_id
            JOIN account_tax at ON aml.tax_line_id = at.id
            JOIN res_partner rp ON am.partner_id = rp.id
            CROSS JOIN params p
            WHERE am.move_type IN ('in_invoice', 'in_refund')
            AND am.state = 'posted'
            AND at.type_tax_use = 'purchase'
            AND at.amount < 0
            AND am.invoice_date BETWEEN p.date_from AND p.date_to
            GROUP BY rp.name, rp.vat, rp.is_company, at.name, at.description, at.amount
            ORDER BY rp.name;
        """,
        'map_summary': """
            WITH params AS (
                SELECT %s::date AS date_from, %s::date AS date_to
            )
            SELECT
                ROW_NUMBER() OVER (ORDER BY rp.name) AS "SEQ",
                rp.vat AS "TAXPAYER IDENTIFICATION NUMBER",
                CASE WHEN rp.is_company THEN rp.name ELSE '' END AS "CORPORATION",
                CASE WHEN NOT rp.is_company THEN rp.name ELSE '' END AS "INDIVIDUAL",
                rp.is_company AS "IS COMPANY",
                rp.last_name AS "LAST NAME",
                rp.first_name AS "FIRST NAME",
                rp.middle_name AS "MIDDLE NAME",
                UPPER(
                    REGEXP_REPLACE(
                        COALESCE(
                            NULLIF(SUBSTRING(at.description->>'en_US' FROM 'W[CI]\\s*[0-9]+'), ''),
                            SUBSTRING(at.name->>'en_US' FROM 'W[CI]\\s*[0-9]+')
                        ),
                        '\\s+', '', 'g'
                    )
                ) AS "ATC CODE",
                NULLIF(TRIM(REGEXP_REPLACE(at.description->>'en_US', '<[^>]+>', '', 'g')), '') AS "NATURE OF PAYMENT",
                SUM(aml.tax_base_amount) AS "AMOUNT OF INCOME PAYMENT",
                CONCAT(ABS(at.amount)::numeric(10,2), '%%') AS "TAX RATE",
                SUM(ABS(aml.credit - aml.debit)) AS "AMOUNT OF TAX WITHHELD"
            FROM account_move_line aml
            JOIN account_move am ON am.id = aml.move_id
            JOIN account_tax at ON aml.tax_line_id = at.id
            JOIN res_partner rp ON am.partner_id = rp.id
            CROSS JOIN params p
            WHERE am.move_type IN ('in_invoice', 'in_refund')
            AND am.state = 'posted'
            AND at.type_tax_use = 'purchase'
            AND at.amount < 0
            AND am.invoice_date BETWEEN p.date_from AND p.date_to
            GROUP BY rp.name, rp.vat, rp.is_company, rp.last_name, rp.first_name, rp.middle_name,
                     at.name, at.description, at.amount
            ORDER BY rp.name;
        """,
    'general_ledger': """
            WITH params AS (
                SELECT %s::date AS date_from, %s::date AS date_to
            ),
            {doc_ctes},
            period_lines AS (
                SELECT
                    aml.id AS line_id, aml.account_id, aml.company_id,
                    am.date AS d, am.name AS jref, (aj.name ->> 'en_US') AS jtype,
                    {doc_expr} AS primary_ref,
                    COALESCE(NULLIF(am.ref, ''), '') AS secondary_ref,
                    COALESCE(aml.name, '') AS particulars,
                    aml.debit, aml.credit
                FROM account_move_line aml
                JOIN account_move am ON am.id = aml.move_id
                JOIN account_journal aj ON aj.id = am.journal_id
                LEFT JOIN cash_dir cd ON cd.move_id = am.id
                LEFT JOIN pay_bill_docs pb ON pb.payment_move_id = am.id
                CROSS JOIN params p
                WHERE am.state = 'posted'
                  AND am.date BETWEEN p.date_from AND p.date_to
            ),
            opening AS (
                SELECT aml.account_id, aml.company_id, SUM(aml.debit - aml.credit) AS bal
                FROM account_move_line aml
                JOIN account_move am ON am.id = aml.move_id
                CROSS JOIN params p
                WHERE am.state = 'posted' AND am.date < p.date_from
                GROUP BY aml.account_id, aml.company_id
            ),
            accts AS (
                SELECT account_id, company_id FROM period_lines
                UNION
                SELECT account_id, company_id FROM opening WHERE bal <> 0
            ),
            opening_rows AS (
                SELECT a.account_id, a.company_id, COALESCE(o.bal, 0) AS bal
                FROM accts a
                LEFT JOIN opening o
                    ON o.account_id = a.account_id AND o.company_id = a.company_id
            )
            SELECT
                x.acct AS "ACCOUNT",
                x.d AS "DATE",
                x.jref AS "JOURNAL REF. NO.",
                x.jtype AS "JOURNAL TYPE",
                x.primary_ref AS "PRIMARY",
                x.secondary_ref AS "SECONDARY",
                x.particulars AS "PARTICULARS",
                x.debit AS "DEBIT",
                x.credit AS "CREDIT"
            FROM (
                SELECT
                    (aa.code_store ->> orow.company_id::text) || ' - ' || (aa.name ->> 'en_US') AS acct,
                    0 AS grp, (p.date_from - 1) AS d, ''::text AS jref,
                    'Beginning Balance'::text AS jtype, ''::text AS primary_ref,
                    ''::text AS secondary_ref, 'Beginning Balance'::text AS particulars,
                    GREATEST(orow.bal, 0) AS debit, GREATEST(-orow.bal, 0) AS credit,
                    0 AS line_id
                FROM opening_rows orow
                JOIN account_account aa ON aa.id = orow.account_id
                CROSS JOIN params p
                UNION ALL
                SELECT
                    (aa.code_store ->> pl.company_id::text) || ' - ' || (aa.name ->> 'en_US'),
                    1, pl.d, pl.jref, pl.jtype, pl.primary_ref, pl.secondary_ref,
                    pl.particulars, pl.debit, pl.credit, pl.line_id
                FROM period_lines pl
                JOIN account_account aa ON aa.id = pl.account_id
            ) x
            ORDER BY x.acct, x.grp, x.d, x.jref, x.line_id;
        """.format(doc_ctes=_DOC_REF_CTES, doc_expr=_DOC_REF_EXPR),
    'sawt': """
        WITH params AS (
            SELECT %s::date AS date_from, %s::date AS date_to
        )
        SELECT
            ROW_NUMBER() OVER (ORDER BY rp.name) AS "SEQ NO",
            rp.vat AS "TAXPAYER IDENTIFICATION NUMBER",
            rp.name AS "REGISTERED NAME",
            rp.is_company AS "IS COMPANY",
            rp.last_name AS "LAST NAME",
            rp.first_name AS "FIRST NAME",
            rp.middle_name AS "MIDDLE NAME",
            p.date_from AS "RETURN PERIOD FROM",
            p.date_to AS "RETURN PERIOD TO",
            UPPER(
                REGEXP_REPLACE(
                    COALESCE(
                        NULLIF(SUBSTRING(at.description->>'en_US' FROM 'W[CI]\\s*[0-9]+'), ''),
                        SUBSTRING(at.name->>'en_US' FROM 'W[CI]\\s*[0-9]+')
                    ),
                    '\\s+', '', 'g'
                )
            ) AS "ATC CODE",
            NULLIF(TRIM(REGEXP_REPLACE(at.description->>'en_US', '<[^>]+>', '', 'g')), '') AS "NATURE OF INCOME PAYMENT",
            SUM(aml.tax_base_amount) AS "AMOUNT",
            CONCAT(ABS(at.amount)::numeric(10,2), '%%') AS "TAX RATE",
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
        GROUP BY rp.name, rp.vat, rp.is_company, rp.last_name, rp.first_name, rp.middle_name,
                 at.name, at.description, at.amount, p.date_from, p.date_to
        ORDER BY rp.name;
    """,
    'qap_summary': """
        WITH params AS (
            SELECT %s::date AS date_from, %s::date AS date_to
        ),
        bounds AS (
            SELECT date_from, date_to,
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
            rp.is_company AS "IS COMPANY",
            rp.last_name AS "LAST NAME",
            rp.first_name AS "FIRST NAME",
            rp.middle_name AS "MIDDLE NAME",
            UPPER(
                REGEXP_REPLACE(
                    COALESCE(
                        NULLIF(SUBSTRING(at.description->>'en_US' FROM 'W[CI]\\s*[0-9]+'), ''),
                        SUBSTRING(at.name->>'en_US' FROM 'W[CI]\\s*[0-9]+')
                    ),
                    '\\s+', '', 'g'
                )
            ) AS "ATC CODE",
            NULLIF(TRIM(REGEXP_REPLACE(at.description->>'en_US', '<[^>]+>', '', 'g')), '') AS "NATURE OF PAYMENT",
            SUM(CASE WHEN am.invoice_date BETWEEN b.m1_start AND b.m1_end THEN aml.tax_base_amount ELSE 0 END) AS "AMOUNT OF INCOME PAYMENT M1",
            CASE WHEN SUM(CASE WHEN am.invoice_date BETWEEN b.m1_start AND b.m1_end THEN aml.tax_base_amount ELSE 0 END) <> 0 THEN ABS(at.amount)::numeric(10,2) || '%%' END AS "TAX RATE M1",
            SUM(CASE WHEN am.invoice_date BETWEEN b.m1_start AND b.m1_end THEN ABS(aml.credit - aml.debit) ELSE 0 END) AS "TAX WITHHELD M1",
            SUM(CASE WHEN am.invoice_date BETWEEN b.m2_start AND b.m2_end THEN aml.tax_base_amount ELSE 0 END) AS "AMOUNT OF INCOME PAYMENT M2",
            CASE WHEN SUM(CASE WHEN am.invoice_date BETWEEN b.m2_start AND b.m2_end THEN aml.tax_base_amount ELSE 0 END) <> 0 THEN ABS(at.amount)::numeric(10,2) || '%%' END AS "TAX RATE M2",
            SUM(CASE WHEN am.invoice_date BETWEEN b.m2_start AND b.m2_end THEN ABS(aml.credit - aml.debit) ELSE 0 END) AS "TAX WITHHELD M2",
            SUM(CASE WHEN am.invoice_date BETWEEN b.m3_start AND b.m3_end THEN aml.tax_base_amount ELSE 0 END) AS "AMOUNT OF INCOME PAYMENT M3",
            CASE WHEN SUM(CASE WHEN am.invoice_date BETWEEN b.m3_start AND b.m3_end THEN aml.tax_base_amount ELSE 0 END) <> 0 THEN ABS(at.amount)::numeric(10,2) || '%%' END AS "TAX RATE M3",
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
        GROUP BY rp.name, rp.vat, rp.is_company, rp.last_name, rp.first_name, rp.middle_name, at.name, at.description, at.amount
        ORDER BY rp.name;
    """,
    'form_1601e': """
        WITH params AS (
            SELECT %s::date AS date_from, %s::date AS date_to
        )
        SELECT
            ROW_NUMBER() OVER (ORDER BY rp.name) AS "SEQ",
            rp.vat AS "TAXPAYER IDENTIFICATION NUMBER",
            CASE WHEN rp.is_company THEN rp.name ELSE '' END AS "CORPORATION",
            CASE WHEN NOT rp.is_company THEN rp.name ELSE '' END AS "INDIVIDUAL",
            rp.is_company AS "IS COMPANY",
            rp.last_name AS "LAST NAME",
            rp.first_name AS "FIRST NAME",
            rp.middle_name AS "MIDDLE NAME",
            UPPER(
                REGEXP_REPLACE(
                    COALESCE(
                        NULLIF(SUBSTRING(at.description->>'en_US' FROM 'W[CI]\\s*[0-9]+'), ''),
                        SUBSTRING(at.name->>'en_US' FROM 'W[CI]\\s*[0-9]+')
                    ),
                    '\\s+', '', 'g'
                )
            ) AS "ATC CODE",
            NULLIF(TRIM(REGEXP_REPLACE(at.description->>'en_US', '<[^>]+>', '', 'g')), '') AS "NATURE OF PAYMENT",
            SUM(aml.tax_base_amount) AS "TOTAL INCOME PAYMENT",
            CONCAT(ABS(at.amount)::numeric(10,2), '%%') AS "TAX RATE",
            SUM(ABS(aml.credit - aml.debit)) AS "TOTAL TAX WITHHELD"
        FROM account_move_line aml
        JOIN account_move am ON am.id = aml.move_id
        JOIN account_tax at ON aml.tax_line_id = at.id
        JOIN res_partner rp ON am.partner_id = rp.id
        CROSS JOIN params p
        WHERE am.move_type IN ('in_invoice', 'in_refund')
        AND am.state = 'posted'
        AND at.type_tax_use = 'purchase'
        AND at.amount < 0
        AND am.invoice_date BETWEEN p.date_from AND p.date_to
        GROUP BY rp.name, rp.vat, rp.is_company, rp.last_name, rp.first_name, rp.middle_name, at.name, at.description, at.amount
        ORDER BY rp.name;
    """,
    'withholding_tax_book': """
        SELECT
            am.invoice_date AS "DATE",
            rp.name AS "NAME OF PAYEE/SUPPLIER",
            rp.contact_address_complete AS "REGISTERED ADDRESS",
            rp.vat AS "TIN",
            am.name AS "REFERENCE",
            am.ref AS "NUMBER",
            am.amount_total AS "GROSS AMOUNT",
            am.amount_tax AS "INPUT TAX (12%%)",
            am.amount_untaxed AS "NET OF VAT",
            CASE WHEN am.amount_tax > 0 THEN 'Y' ELSE 'N' END AS "INPUT TAX ALLOWED",
            aa.name->>'en_US' AS "ACCOUNT TITLE",
            aml.tax_base_amount AS "TAX BASE",
            UPPER(
                REGEXP_REPLACE(
                    COALESCE(
                        NULLIF(SUBSTRING(at.description->>'en_US' FROM 'W[CI]\\s*[0-9]+'), ''),
                        SUBSTRING(at.name->>'en_US' FROM 'W[CI]\\s*[0-9]+')
                    ),
                    '\\s+', '', 'g'
                )
            ) AS "ATC",
            ABS(at.amount) AS "EWT RATE",
            ABS(aml.credit - aml.debit) AS "AMOUNT"
        FROM account_move_line aml
        JOIN account_move am ON am.id = aml.move_id
        JOIN account_tax at ON at.id = aml.tax_line_id
        JOIN res_partner rp ON rp.id = am.partner_id
        LEFT JOIN account_move_line expense_aml
            ON expense_aml.move_id = am.id
        AND expense_aml.tax_line_id IS NULL
        AND expense_aml.product_id IS NOT NULL
        LEFT JOIN account_account aa ON aa.id = expense_aml.account_id
        WHERE am.move_type = 'in_invoice'
        AND am.state = 'posted'
        AND at.type_tax_use = 'purchase'
        AND at.amount < 0
        AND am.invoice_date BETWEEN %s AND %s
        ORDER BY am.invoice_date, am.ref;
    """,
    'aging_ap': """
        WITH params AS (
            SELECT %s::date AS date_from, %s::date AS date_to
        ),
        ap_line AS (
            SELECT aml.move_id, SUM(aml.balance) * -1 AS outstanding_amount
            FROM account_move_line aml
            JOIN account_account aa ON aa.id = aml.account_id
            WHERE aa.account_type = 'liability_payable'
            GROUP BY aml.move_id
        ),
        expense_line AS (
            SELECT DISTINCT ON (aml.move_id)
                aml.move_id,
                aml.name AS particulars,
                aa.name->>'en_US' AS account_title
            FROM account_move_line aml
            JOIN account_account aa ON aa.id = aml.account_id
            WHERE aml.tax_line_id IS NULL AND aml.product_id IS NOT NULL
            ORDER BY aml.move_id, aml.id
        ),
        line_values AS (
            SELECT
                l.id, l.move_id, l.price_subtotal,
                BOOL_OR(tag.name->>'en_US' ILIKE '%%46E%%') AS is_exempt,
                BOOL_OR(tag.name->>'en_US' ILIKE '%%46ZR%%') AS is_zero_rated,
                BOOL_OR(tag.name->>'en_US' ILIKE '%%42A%%') AS is_taxable
            FROM account_move_line l
            LEFT JOIN account_account_tag_account_move_line_rel tagrel ON tagrel.account_move_line_id = l.id
            LEFT JOIN account_account_tag tag ON tag.id = tagrel.account_account_tag_id
            WHERE l.product_id IS NOT NULL
            GROUP BY l.id, l.move_id, l.price_subtotal
        ),
        line_totals AS (
            SELECT
                move_id,
                SUM(CASE WHEN is_exempt THEN price_subtotal ELSE 0 END) AS exempt_amount,
                SUM(CASE WHEN is_zero_rated THEN price_subtotal ELSE 0 END) AS zero_rated_amount,
                SUM(CASE WHEN is_taxable THEN price_subtotal ELSE 0 END) AS vatable_amount
            FROM line_values
            GROUP BY move_id
        ),
        ewt AS (
            SELECT
                aml.move_id,
                MAX(ABS(at.amount)) / 100.0 AS ewt_rate,
                SUM(ABS(aml.credit - aml.debit)) AS ewt_payable
            FROM account_move_line aml
            JOIN account_tax at ON at.id = aml.tax_line_id
            WHERE at.type_tax_use = 'purchase' AND at.amount < 0
            GROUP BY aml.move_id
        )
        SELECT
            am.invoice_date AS "AP DATE",
            am.name AS "AP ENTRY NUMBER",
            rp.name AS "NAME OF SUPPLIER",
            COALESCE(el.particulars, '') AS "PARTICULARS",
            COALESCE(el.account_title, '') AS "ACCOUNT TITLE",
            am.invoice_date AS "REFERENCE DATE",
            am.ref AS "SALES INVOICE",
            '' AS "BILLING",
            '' AS "OTHERS",
            COALESCE(lt.vatable_amount, am.amount_untaxed) AS "VATABLE",
            COALESCE(lt.zero_rated_amount, 0) AS "ZERO-RATED",
            COALESCE(lt.exempt_amount, 0) AS "EXEMPT",
            am.amount_tax AS "12%% VAT",
            am.amount_total AS "TOTAL",
            COALESCE(ewt.ewt_rate, 0) AS "EWT RATE",
            COALESCE(ewt.ewt_payable, 0) AS "EWT PAYABLE",
            (am.amount_total - COALESCE(ewt.ewt_payable, 0)) AS "AMOUNT DUE",
            am.invoice_date_due AS "DUE DATE",
            CURRENT_DATE AS "REPORT DATE",
            (CURRENT_DATE - am.invoice_date_due) AS "N0. OF DAYS OVERDUE",
            CASE WHEN am.invoice_date_due >= CURRENT_DATE THEN ap_line.outstanding_amount ELSE 0 END AS "CURRENT",
            CASE WHEN (CURRENT_DATE - am.invoice_date_due) BETWEEN 1 AND 30 THEN ap_line.outstanding_amount ELSE 0 END AS "[01-30]",
            CASE WHEN (CURRENT_DATE - am.invoice_date_due) BETWEEN 31 AND 60 THEN ap_line.outstanding_amount ELSE 0 END AS "[31-60]",
            CASE WHEN (CURRENT_DATE - am.invoice_date_due) BETWEEN 61 AND 90 THEN ap_line.outstanding_amount ELSE 0 END AS "[61-90]",
            CASE WHEN (CURRENT_DATE - am.invoice_date_due) BETWEEN 91 AND 120 THEN ap_line.outstanding_amount ELSE 0 END AS "[91-120]",
            CASE WHEN (CURRENT_DATE - am.invoice_date_due) > 120 THEN ap_line.outstanding_amount ELSE 0 END AS "[121-OVER]"
        FROM account_move am
        JOIN res_partner rp ON rp.id = am.partner_id
        JOIN ap_line ON ap_line.move_id = am.id
        LEFT JOIN expense_line el ON el.move_id = am.id
        LEFT JOIN line_totals lt ON lt.move_id = am.id
        LEFT JOIN ewt ON ewt.move_id = am.id
        CROSS JOIN params p
        WHERE am.move_type = 'in_invoice'
            AND am.state = 'posted'
            AND am.payment_state IN ('not_paid', 'partial')
            AND ap_line.outstanding_amount <> 0
        ORDER BY rp.name, am.invoice_date_due;
    """,
    'aging_ar': """
        WITH params AS (
            SELECT %s::date AS date_from, %s::date AS date_to
        ),
        ar_line AS (
            SELECT aml.move_id, SUM(aml.balance) AS outstanding_amount
            FROM account_move_line aml
            JOIN account_account aa ON aa.id = aml.account_id
            WHERE aa.account_type = 'asset_receivable'
            GROUP BY aml.move_id
        )
        SELECT
            am.invoice_date AS "SI DATE",
            rp.name AS "NAME OF CUSTOMER",
            COALESCE(sp.name, '') AS "SALES REPRESENTATIVE",
            am.name AS "REFERENCE",
            '' AS "DELIVERY RECEIPT",
            '' AS "OTHERS",
            COALESCE(pt.name->>'en_US', '') AS "TERMS",
            1 AS "FX RATE",
            0 AS "SALES INVOICE AMOUNT (USD)",
            am.amount_total AS "SALES INVOICE AMOUNT (PHP)",
            am.invoice_date_due AS "DUE DATE",
            CURRENT_DATE AS "REPORT DATE",
            (CURRENT_DATE - am.invoice_date_due) AS "NO. OF DAYS OVERDUE",
            CASE WHEN am.invoice_date_due >= CURRENT_DATE THEN ar_line.outstanding_amount ELSE 0 END AS "CURRENT",
            CASE WHEN (CURRENT_DATE - am.invoice_date_due) BETWEEN 1 AND 30 THEN ar_line.outstanding_amount ELSE 0 END AS "[01-30]",
            CASE WHEN (CURRENT_DATE - am.invoice_date_due) BETWEEN 31 AND 60 THEN ar_line.outstanding_amount ELSE 0 END AS "[31-60]",
            CASE WHEN (CURRENT_DATE - am.invoice_date_due) BETWEEN 61 AND 90 THEN ar_line.outstanding_amount ELSE 0 END AS "[61-90]",
            CASE WHEN (CURRENT_DATE - am.invoice_date_due) BETWEEN 91 AND 120 THEN ar_line.outstanding_amount ELSE 0 END AS "[91-120]",
            CASE WHEN (CURRENT_DATE - am.invoice_date_due) > 120 THEN ar_line.outstanding_amount ELSE 0 END AS "[121-over]",
            ar_line.outstanding_amount AS "TOTAL RECEIVABLE"
        FROM account_move am
        JOIN res_partner rp ON rp.id = am.partner_id
        JOIN ar_line ON ar_line.move_id = am.id
        LEFT JOIN account_payment_term pt ON pt.id = am.invoice_payment_term_id
        LEFT JOIN res_users ru ON am.invoice_user_id = ru.id
        LEFT JOIN res_partner sp ON ru.partner_id = sp.id
        CROSS JOIN params p
        WHERE am.move_type = 'out_invoice'
            AND am.state = 'posted'
            AND am.payment_state IN ('not_paid', 'partial')
            AND ar_line.outstanding_amount <> 0
        ORDER BY rp.name, am.invoice_date_due;
    """,
    'form_1604e': """
        WITH params AS (
            SELECT %s::date AS date_from, %s::date AS date_to
        )
        SELECT
            ROW_NUMBER() OVER (ORDER BY rp.name) AS "SEQ",
            rp.vat AS "TAXPAYER IDENTIFICATION NUMBER",
            rp.name AS "REGISTERED NAME",
            UPPER(
                REGEXP_REPLACE(
                    COALESCE(
                        NULLIF(SUBSTRING(at.description->>'en_US' FROM 'W[CI]\\s*[0-9]+'), ''),
                        SUBSTRING(at.name->>'en_US' FROM 'W[CI]\\s*[0-9]+')
                    ),
                    '\\s+', '', 'g'
                )
            ) AS "ATC CODE",
            NULLIF(TRIM(REGEXP_REPLACE(at.description->>'en_US', '<[^>]+>', '', 'g')), '') AS "NATURE OF PAYMENT",
            CONCAT(ABS(at.amount)::numeric(10,2), '%%') AS "RATE OF TAX",
            SUM(aml.tax_base_amount) AS "AMOUNT OF INCOME PAYMENT",
            SUM(ABS(aml.credit - aml.debit)) AS "AMOUNT OF TAX WITHHELD"
        FROM account_move_line aml
        JOIN account_move am ON am.id = aml.move_id
        JOIN account_tax at ON aml.tax_line_id = at.id
        JOIN res_partner rp ON am.partner_id = rp.id
        CROSS JOIN params p
        WHERE am.move_type IN ('in_invoice', 'in_refund')
        AND am.state = 'posted'
        AND at.type_tax_use = 'purchase'
        AND at.amount < 0
        AND am.invoice_date BETWEEN p.date_from AND p.date_to
        GROUP BY rp.name, rp.vat, at.name, at.description, at.amount
        ORDER BY rp.name;
    """,
}
SQL_QUERIES['form_1601eq'] = SQL_QUERIES['qap_summary']

# Reports that support the .DAT export
DAT_SUPPORTED_REPORTS = (
    'qap_summary', 'form_1604e', 'vat_summary_sales', 'vat_summary_purchase',
    'form_1601e', 'form_1601eq', 'map_summary', 'sawt',
)

class ResCompany(models.Model):
    _inherit = 'res.company'
    rdo_code = fields.Char("RDO Code")

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
    journal_id = fields.Many2one("account.journal", string="Sales Journal",domain="[('type', '=', 'sale')]",)
    cash_flow_journal_ids = fields.Many2many(
        "account.journal",
        "custom_sql_report_cash_flow_journal_rel",
        "report_id",
        "journal_id",
        string="Cash Flow Journals",
        domain="[('type', 'in', ('bank', 'cash', 'general')), ('company_id', 'in', allowed_company_ids)]",
    )
    trial_balance_journal_ids = fields.Many2many(
        "account.journal",
        "custom_sql_report_trial_balance_journal_rel",
        "report_id",
        "journal_id",
        string="Trial Balance Journals",
        domain="[('company_id', 'in', allowed_company_ids)]",
    )
    deferred_expense_journal_ids = fields.Many2many(
        "account.journal",
        "custom_sql_report_deferred_expense_journal_rel",
        "report_id",
        "journal_id",
        string="Deferred Expense Journals",
        domain="[('company_id', 'in', allowed_company_ids)]",
    )
    partner_ledger_journal_ids = fields.Many2many(
        "account.journal",
        "custom_sql_report_partner_ledger_journal_rel",
        "report_id",
        "journal_id",
        string="Partner Ledger Journals",
        domain="[('company_id', 'in', allowed_company_ids)]",
    )
    partner_ledger_partner_ids = fields.Many2many(
        "res.partner",
        "custom_sql_report_partner_ledger_partner_rel",
        "report_id",
        "partner_id",
        string="Partners",
    )

    sql_query = fields.Text("SQL Query")
    
    result_columns = fields.Text("Result Columns")  # JSON array
    filter_by_year = fields.Boolean(string="Filter By year", tracking=True, default=False ,help="Checking this will allow you to filter by year.")
    year = fields.Selection(
        [(str(y), str(y)) for y in range(2000, 2051)],
        string="Year",
        default=str(date.today().year),
        help="Select a year to auto-fill From Date and To Date"
    )

    sawt_form_type = fields.Selection(
        SAWT_FORM_TYPES,
        string="SAWT Attached To",
        default='1702Q',
        help="Tax return this SAWT will be attached to; used as the form code in the DAT file.",
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

    def _get_cash_flow_statement_rows(self):
        self.ensure_one()
        if not self.from_date or not self.to_date or self.from_date > self.to_date:
            raise ValidationError("Select a valid Cash Flow Statement date range.")

        report = self.env.ref('account_reports.cash_flow_report', raise_if_not_found=False)
        if not report:
            raise UserError("The Odoo Cash Flow Statement report is unavailable. Install the account_reports module.")

        previous_options = {
            'selected_variant_id': report.id,
            'date': {
                'date_from': fields.Date.to_string(self.from_date),
                'date_to': fields.Date.to_string(self.to_date),
                'mode': 'range',
                'filter': 'custom',
            },
            'show_account': True,
            'show_currency': True,
        }
        journals = self.cash_flow_journal_ids.filtered(
            lambda journal: journal.company_id == self.env.company
            and journal.type in ('bank', 'cash', 'general')
        )
        if journals:
            previous_options['journals'] = [
                {'id': journal.id, 'model': 'account.journal', 'selected': True}
                for journal in journals
            ]

        options = report.get_options(previous_options)
        report_lines = report._get_lines(options)
        rows = []
        for line in report_lines:
            columns = line.get('columns') or []
            balance = columns[0].get('no_format', 0.0) if columns else 0.0
            label = f"{'  ' * line.get('level', 0)}{line.get('name', '')}"
            rows.append((label, balance or 0.0))
        return ['Cash Flow Statement', 'Balance'], rows

    def _get_trial_balance_rows(self):
        self.ensure_one()
        if not self.from_date or not self.to_date or self.from_date > self.to_date:
            raise ValidationError("Select a valid Trial Balance date range.")

        report = self.env.ref('account_reports.trial_balance_report', raise_if_not_found=False)
        if not report:
            raise UserError("The Odoo Trial Balance report is unavailable. Install the account_reports module.")

        previous_options = {
            'selected_variant_id': report.id,
            'date': {
                'date_from': fields.Date.to_string(self.from_date),
                'date_to': fields.Date.to_string(self.to_date),
                'mode': 'range',
                'filter': 'custom',
            },
            'show_account': True,
            'show_currency': True,
        }
        journals = self.trial_balance_journal_ids.filtered(
            lambda journal: journal.company_id == self.env.company
        )
        if journals:
            previous_options['journals'] = [
                {'id': journal.id, 'model': 'account.journal', 'selected': True}
                for journal in journals
            ]

        options = report.get_options(previous_options)
        report_lines = report._get_lines(options)
        columns = [
            'Code',
            'Account Title',
            'Category',
            'Normal',
            'Beginning Balance Debit',
            'Beginning Balance Credit',
            'Balances for This Period Debit',
            'Balances for This Period Credit',
            'Trial Balances Debit',
            'Trial Balances Credit',
        ]
        rows = []
        for line in report_lines:
            balance_columns = line.get('columns') or []
            balances = [column.get('no_format') or 0.0 for column in balance_columns]
            balances = (balances + [0.0] * 6)[:6]
            model, record_id = report._get_model_info_from_id(line['id'])
            if model == 'account.account' and record_id:
                account = self.env['account.account'].browse(record_id)
                account_type = account.account_type or ''
                account_type_selection = dict(account._fields['account_type']._description_selection(self.env))
                category = account_type_selection.get(account_type, account_type)
                normal = 'Debit' if account.internal_group in ('asset', 'expense') else 'Credit'
                if 'contra' in account_type:
                    normal = 'Credit' if normal == 'Debit' else 'Debit'
                code = account.code or ''
                title = account.name or line.get('name', '')
            else:
                code = ''
                title = line.get('name', '')
                category = ''
                normal = ''
            title = f"{'  ' * line.get('level', 0)}{title}"
            rows.append(tuple([code, title, category, normal, *balances]))
        return columns, rows

    def _get_generic_tax_report_rows(self):
        self.ensure_one()
        if not self.from_date or not self.to_date or self.from_date > self.to_date:
            raise ValidationError("Select a valid Generic Tax Report date range.")

        report = self.env.ref('account.generic_tax_report', raise_if_not_found=False)
        if not report:
            raise UserError("The Odoo Generic Tax Report is unavailable. Install the account_reports module.")

        previous_options = {
            'selected_variant_id': report.id,
            'date': {
                'date_from': fields.Date.to_string(self.from_date),
                'date_to': fields.Date.to_string(self.to_date),
                'mode': 'range',
                'filter': 'custom',
            },
            'show_account': True,
            'show_currency': True,
        }
        options = report.get_options(previous_options)
        report_lines = report._get_lines(options)
        report_columns = options.get('columns', [])
        columns = ['Tax Report'] + [
            column.get('name') or column.get('expression_label') or 'Amount'
            for column in report_columns
        ]

        rows = []
        for line in report_lines:
            line_columns = line.get('columns') or []
            amounts = [column.get('no_format') for column in line_columns]
            amounts = (amounts + [None] * len(report_columns))[:len(report_columns)]
            label = f"{'  ' * line.get('level', 0)}{line.get('name', '')}"
            rows.append(tuple([label, *amounts]))
        return columns, rows

    def _get_deferred_expense_report_rows(self):
        self.ensure_one()
        if not self.from_date or not self.to_date or self.from_date > self.to_date:
            raise ValidationError("Select a valid Deferred Expense Report date range.")

        report = self.env.ref('account_reports.deferred_expense_report', raise_if_not_found=False)
        if not report:
            raise UserError("The Odoo Deferred Expense Report is unavailable. Install the account_reports module.")

        previous_options = {
            'selected_variant_id': report.id,
            'date': {
                'date_from': fields.Date.to_string(self.from_date),
                'date_to': fields.Date.to_string(self.to_date),
                'mode': 'range',
                'filter': 'custom',
            },
            'show_account': True,
            'show_currency': True,
        }
        journals = self.deferred_expense_journal_ids.filtered(
            lambda journal: journal.company_id == self.env.company
        )
        if journals:
            previous_options['journals'] = [
                {'id': journal.id, 'model': 'account.journal', 'selected': True}
                for journal in journals
            ]

        options = report.get_options(previous_options)
        report_lines = report._get_lines(options)
        report_columns = options.get('columns', [])
        columns = ['Deferred Expense'] + [
            column.get('name') or column.get('expression_label') or 'Amount'
            for column in report_columns
        ]

        rows = []
        for line in report_lines:
            line_columns = line.get('columns') or []
            amounts = [column.get('no_format') for column in line_columns]
            amounts = (amounts + [None] * len(report_columns))[:len(report_columns)]
            label = f"{'  ' * line.get('level', 0)}{line.get('name', '')}"
            rows.append(tuple([label, *amounts]))
        return columns, rows

    def _get_partner_ledger_rows(self):
        self.ensure_one()
        if not self.from_date or not self.to_date or self.from_date > self.to_date:
            raise ValidationError("Select a valid Partner Ledger date range.")

        report = self.env.ref('account_reports.partner_ledger_report', raise_if_not_found=False)
        if not report:
            raise UserError("The Odoo Partner Ledger report is unavailable. Install the account_reports module.")

        previous_options = {
            'selected_variant_id': report.id,
            'date': {
                'date_from': fields.Date.to_string(self.from_date),
                'date_to': fields.Date.to_string(self.to_date),
                'mode': 'range',
                'filter': 'custom',
            },
            'show_account': True,
            'show_currency': True,
            'partner_ids': self.partner_ledger_partner_ids.ids,
        }
        journals = self.partner_ledger_journal_ids.filtered(
            lambda journal: journal.company_id == self.env.company
        )
        if journals:
            previous_options['journals'] = [
                {'id': journal.id, 'model': 'account.journal', 'selected': True}
                for journal in journals
            ]

        options = report.get_options(previous_options)
        options['unfold_all'] = True
        options['export_mode'] = 'print'
        report_lines = report._get_lines(options)
        report_columns = options.get('columns', [])
        columns = ['Partner / Journal Item'] + [
            column.get('name') or column.get('expression_label') or 'Amount'
            for column in report_columns
        ]

        rows = []
        for line in report_lines:
            line_columns = line.get('columns') or []
            amounts = [column.get('no_format') for column in line_columns]
            amounts = (amounts + [None] * len(report_columns))[:len(report_columns)]
            label = f"{'  ' * line.get('level', 0)}{line.get('name', '')}"
            rows.append(tuple([label, *amounts]))
        return columns, rows

    def _is_summary_total_row(self, row, columns):
        if not columns:
            return False
        label = str(row.get(columns[0], '')).strip()
        if self.name == 'trial_balance':
            title = str(row.get('Account Title', '')).strip()
            return label.lower() == 'total' or title.lower() == 'total'
        if self.name == 'cash_flow_statement':
            return label in {
                'Cash and cash equivalents, beginning of period',
                'Net increase in cash and cash equivalents',
                'Cash flows from operating activities',
                'Cash flows from investing & extraordinary activities',
                'Cash flows from financing activities',
                'Cash flows from unclassified activities',
                'Cash and cash equivalents, closing balance',
            }
        if self.name == 'generic_tax_report':
            return label in ('Sales', 'Purchases') or label.startswith('Total ')
        if self.name == 'deferred_expense_report':
            return label.lower() == 'total'
        if self.name == 'partner_ledger':
            return label == 'Total' or label.startswith('Total ')
        return False

    """ Generate Report Action"""
    def action_execute_query(self):
        self.ensure_one()

        if self.report_category not in ['books', 'annex', 'other_reports', 'tax_returns']:
            raise ValidationError("Please select a valid report category.")

        try:
            if self.name == 'cash_flow_statement':
                columns, rows = self._get_cash_flow_statement_rows()
            elif self.name == 'trial_balance':
                columns, rows = self._get_trial_balance_rows()
            elif self.name == 'generic_tax_report':
                columns, rows = self._get_generic_tax_report_rows()
            elif self.name == 'deferred_expense_report':
                columns, rows = self._get_deferred_expense_report_rows()
            elif self.name == 'partner_ledger':
                columns, rows = self._get_partner_ledger_rows()
            else:
                sql = SQL_QUERIES.get(self.name)
                if not sql:
                    raise ValidationError("Please select a valid report.")
                self.env.cr.execute(sql, (self.from_date, self.to_date))
                columns = [desc[0] for desc in self.env.cr.description]
                rows = self.env.cr.fetchall()

            serializable_rows, row_html_parts = [], []
            numeric_totals = {col: 0 for col in columns}

            for row in rows:
                row_dict, cells = {}, []
                is_summary_total = self._is_summary_total_row(dict(zip(columns, row)), columns)
                for col, val in zip(columns, row):
                    if isinstance(val, bool):
                        # bool is a subclass of int — must be checked first,
                        # otherwise True/False get formatted as "1"/"0" and summed in totals
                        val = 'Y' if val else 'N'
                    elif isinstance(val, (datetime, date)):
                        val = val.isoformat()
                    elif isinstance(val, (int, float)):
                        numeric_totals[col] = numeric_totals.get(col, 0) + (val or 0)
                        formatted_val = f"{val:,.2f}" if isinstance(val, float) else f"{val:,}"
                        val = formatted_val
                    row_dict[col] = val
                    align = "right" if isinstance(val, str) and val.replace(",", "").replace(".", "").isdigit() else "left"
                    whitespace = "white-space:pre;" if (
                        self.name == 'trial_balance' and col == 'Account Title'
                        or self.name in ('generic_tax_report', 'deferred_expense_report', 'partner_ledger') and col == columns[0]
                    ) else ""
                    weight = "font-weight:bold;" if is_summary_total else ""
                    cells.append(f"<td style='min-width:150px; text-align:{align}; {whitespace} {weight}'>{val or ''}</td>")
                serializable_rows.append(row_dict)
                row_style = "font-weight:bold;" if is_summary_total else ""
                row_html_parts.append(f"<tr style='{row_style}'>{''.join(cells)}</tr>")

            # --- Compute Totals ---
            total_cells = []
            for idx, col in enumerate(columns):
                if idx == 0:
                    total_cells.append("<td style='font-weight:bold;'>TOTAL</td>")
                    continue
                total_val = numeric_totals.get(col)
                if isinstance(total_val, (int, float)) and total_val != 0:
                    formatted_total = f"{total_val:,.2f}"
                    total_cells.append(f"<td style='font-weight:bold; text-align:right;'>{formatted_total}</td>")
                else:
                    total_cells.append("<td></td>")

            total_footer_html = ""
            if self.name not in ('cash_flow_statement', 'trial_balance', 'generic_tax_report', 'deferred_expense_report', 'partner_ledger'):
                total_footer_html = f"""
                    <tfoot style="position: sticky; bottom: 0; background-color: #f0f0f0; z-index: 2; font-weight: bold;">
                        <tr>{''.join(total_cells)}</tr>
                    </tfoot>
                """

            # --- Build Final Table ---
            table_html = f"""
                <div  class="o_sql_report_result" style="
                    width: 100%;
                    max-width: 100%;
                    max-height: 600px;
                    overflow-x: auto;
                    overflow-y: auto;
                    border: 1px solid #ccc;
                    border-radius: 6px;
                    position: relative;
                ">
                    <table class="table table-sm table-bordered" 
                        style="width:100%; border-collapse: collapse; table-layout: auto;">
                        <thead style="position: sticky; top: 0; background-color: #f8f9fa; z-index: 2;">
                            <tr>
                                {"".join(f"<th style='min-width:150px; background-color:#f8f9fa; text-align:center; border:1px solid #dee2e6; position: sticky; top: 0;'>{col}</th>" for col in columns)}
                            </tr>
                        </thead>
                        <tbody>
                            {''.join(row_html_parts)}
                        </tbody>
                        {total_footer_html}
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
        rows = [item for r in self.result_ids for item in json.loads(r.data or "[]")]
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
        bold_text_format = workbook.add_format({'bold': True, 'text_wrap': True, 'valign': 'top', 'border': 1, 'align': 'left'})
        bold_number_format = workbook.add_format({'bold': True, 'num_format': '#,##0.00', 'border': 1, 'align': 'right', 'valign': 'top'})
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
            is_summary_total = self._is_summary_total_row(row, columns)
            row_text_format = bold_text_format if is_summary_total else text_format
            row_number_format = bold_number_format if is_summary_total else number_format
            for col_idx, col_name in enumerate(columns):
                val = row.get(col_name, "")
                
                # Handle numeric values
                if isinstance(val, (int, float)):
                    totals[col_name] += val
                    if val == 0:
                        worksheet.write(row_idx, col_idx, "-", row_text_format)
                    else:
                        worksheet.write_number(row_idx, col_idx, val, row_number_format)
                else:
                    # Try parsing numeric strings (e.g., "1234" or "1,234.56")
                    try:
                        float_val = float(str(val).replace(",", ""))
                        totals[col_name] += float_val
                        if float_val == 0:
                            worksheet.write(row_idx, col_idx, "-", row_text_format)
                        else:
                            worksheet.write_number(row_idx, col_idx, float_val, row_number_format)
                    except Exception:
                        worksheet.write(row_idx, col_idx, val, row_text_format)


        if self.name not in ('cash_flow_statement', 'trial_balance', 'generic_tax_report', 'deferred_expense_report', 'partner_ledger'):
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

    # -------------------------
    # DAT export helpers
    # -------------------------
    @staticmethod
    def _dat_to_float(val):
        if val in (None, '', '-'):
            return 0.0
        try:
            return float(str(val).replace(',', ''))
        except ValueError:
            return 0.0

    @staticmethod
    def _dat_is_company(val):
        if isinstance(val, bool):
            return val
        return str(val or '').strip().lower() in ('1', 'true', 't', 'y', 'yes')

    def _build_alphalist_dat_lines(self, rows, dat_prefix, form_code, period):
        """Builds BIR alphalist DAT lines (H / D1 / C1 records) shared by the
        QAP-for-1601EQ and MAP-for-1601E per-payee-per-ATC exports."""

        def _to_float(val):
            if val in (None, '', '-'):
                return 0.0
            try:
                return float(str(val).replace(',', ''))
            except ValueError:
                return 0.0

        company = self.env.company
        agent_tin = (company.vat or '').replace('-', '')
        branch = '0000'

        lines = [
            f"{dat_prefix},H{form_code},{agent_tin},{branch},{company.name or ''},"
            f"{period},{company.rdo_code or ''},,,,,,,"
        ]

        total_income = 0.0
        total_tax = 0.0
        for idx, row in enumerate(rows, start=1):
            is_company = self._dat_is_company(row.get("IS COMPANY"))
            if is_company:
                name = row.get("CORPORATION") or ''
                last, first, middle = '', '', ''
            else:
                name = ''
                last = row.get("LAST NAME") or ''
                first = row.get("FIRST NAME") or ''
                middle = row.get("MIDDLE NAME") or ''

            payee_tin = (row.get("TAXPAYER IDENTIFICATION NUMBER") or '').replace('-', '')
            atc = row.get("ATC CODE") or ''
            rate_str = (
                row.get("TAX RATE M1") or row.get("TAX RATE M2") or row.get("TAX RATE M3")
                or row.get("TAX RATE") or '0%'
            ).replace('%', '')
            try:
                rate_val = float(rate_str)
            except ValueError:
                rate_val = 0.0
            rate = f"{rate_val:g}"

            # QAP/1601E use "TOTAL ..." columns; MAP Summary uses "AMOUNT OF ..." columns
            income = _to_float(row.get("TOTAL INCOME PAYMENT") or row.get("AMOUNT OF INCOME PAYMENT"))
            tax = _to_float(row.get("TOTAL TAX WITHHELD") or row.get("AMOUNT OF TAX WITHHELD"))
            total_income += income
            total_tax += tax

            lines.append(
                f"D1,{form_code},{idx},{payee_tin},0000,{name},{last},{first},{middle},"
                f"{period},{atc},{rate},{income:.2f},{tax:.2f}"
            )

        lines.append(
            f"C1,{form_code},{agent_tin},{branch},{period},"
            f"{total_income:.2f},{total_tax:.2f},,,,,,,"
        )
        return lines

    def _build_sawt_dat_lines(self, rows, form_code, period):
        """BIR SAWT DAT: HSAWT header / DSAWT per payor-per-ATC / CSAWT control."""

        def _clean(text):
            # quotes/newlines would break the comma-separated record
            return str(text or '').replace('"', '').replace('\n', ' ').strip()

        company = self.env.company
        agent_tin = (company.vat or '').replace('-', '')
        branch = '0000'

        lines = [
            f'HSAWT,H{form_code},{agent_tin},{branch},"{_clean(company.name)}","","","",'
            f'{period},{company.rdo_code or ""}'
        ]

        total_income = 0.0
        total_tax = 0.0
        for idx, row in enumerate(rows, start=1):
            if self._dat_is_company(row.get("IS COMPANY")):
                name = _clean(row.get("REGISTERED NAME"))
                last = first = middle = ''
            else:
                name = ''
                last = _clean(row.get("LAST NAME"))
                first = _clean(row.get("FIRST NAME"))
                middle = _clean(row.get("MIDDLE NAME"))
                if not (last or first):
                    # partner has no split name filled in — fall back to the full name
                    last = _clean(row.get("REGISTERED NAME"))

            payor_tin = (row.get("TAXPAYER IDENTIFICATION NUMBER") or '').replace('-', '')
            atc = row.get("ATC CODE") or ''
            rate = self._dat_to_float((row.get("TAX RATE") or '0').replace('%', ''))
            # sale-side CWT lines are debits, so credit - debit comes out negative
            income = abs(self._dat_to_float(row.get("AMOUNT")))
            tax = abs(self._dat_to_float(row.get("TAX WITHHELD")))
            total_income += income
            total_tax += tax

            lines.append(
                f'DSAWT,D{form_code},{idx},{payor_tin},{branch},"{name}","{last}","{first}","{middle}",'
                f'{period},{atc},{rate:.2f},{income:.2f},{tax:.2f}'
            )

        lines.append(
            f'CSAWT,C{form_code},{agent_tin},{branch},{period},{total_income:.2f},{total_tax:.2f}'
        )
        return lines

    def action_export_dat(self):
        self.ensure_one()
        if not self.result_ids:
            raise UserError("No data to export. Execute a query first.")
        if self.name not in DAT_SUPPORTED_REPORTS:
            raise UserError(
                "DAT export is currently only supported for the QAP Summary, MAP Summary List, SAWT, "
                "Form 1604E, VAT Summary Sales, VAT Summary Purchase, Form 1601E, and Form 1601EQ reports."
            )

        def _to_float(val):
            if val in (None, '', '-'):
                return 0.0
            try:
                return float(str(val).replace(',', ''))
            except ValueError:
                return 0.0

        columns, rows = self.get_table_data()
        company = self.env.company
        agent_tin = (company.vat or '').replace('-', '')
        branch = '0000'

        if self.name in ('qap_summary', 'form_1601eq'):
            period = self.from_date.strftime('%m/%Y')
            lines = self._build_alphalist_dat_lines(rows, dat_prefix='HQAP', form_code='1601EQ', period=period)

        elif self.name == 'form_1601e':
            period = self.from_date.strftime('%m/%Y')
            lines = self._build_alphalist_dat_lines(rows, dat_prefix='HMAP', form_code='1601E', period=period)

        elif self.name == 'map_summary':
            period = self.to_date.strftime('%m/%Y')
            lines = self._build_alphalist_dat_lines(rows, dat_prefix='HMAP', form_code='1601E', period=period)

        elif self.name == 'sawt':
            if not self.sawt_form_type:
                raise UserError("Please select which tax return this SAWT is attached to.")
            period = self.to_date.strftime('%m/%Y')
            lines = self._build_sawt_dat_lines(rows, form_code=self.sawt_form_type, period=period)

        elif self.name == 'form_1604e':
            period = self.to_date.strftime('%m/%d/%Y')
            lines = [f"H1604E,{agent_tin},{branch},{period},N,0,000"]

            total_tax = 0.0
            for idx, row in enumerate(rows, start=1):
                payee_tin = (row.get("TAXPAYER IDENTIFICATION NUMBER") or '').replace('-', '')
                name = row.get("REGISTERED NAME") or row.get("NAME OF PAYEES") or ''
                # Not split into last/first/middle by this report's query yet — see note above.
                last, first, middle = '', '', ''
                atc = row.get("ATC CODE") or ''
                rate_str = (row.get("RATE OF TAX") or '0%').replace('%', '')
                try:
                    rate = float(rate_str)
                except ValueError:
                    rate = 0

                income = _to_float(row.get("AMOUNT OF INCOME PAYMENT"))
                tax = _to_float(row.get("AMOUNT OF TAX WITHHELD"))
                total_tax += tax

                lines.append(
                    f'D4,1604E,{agent_tin},{branch},{period},{idx},{payee_tin},{branch},'
                    f'"{name}",{last},{first},{middle},{atc},{income:.2f},{rate:.2f},{tax:.2f}'
                )

            lines.append(f"C4,1604E,{agent_tin},{branch},{period},{total_tax:.2f}")

        elif self.name == 'vat_summary_sales':
            period = self.to_date.strftime('%m/%d/%Y')
            company_tin = agent_tin
            company_name = company.name or ''
            trade_name = company.name or ''
            region_code = ''
            region_name = ''
            rdo_code = company.rdo_code or ''

            total_exempt = 0.0
            total_zero_rated = 0.0
            total_vatable = 0.0
            total_vat = 0.0
            detail_lines = []

            for row in rows:
                customer_tin = (row.get("TAX PAYER IDENTIFICATION NUMBER") or '').replace('-', '')
                customer_name = row.get("REGISTERED NAME") or ''
                exempt = _to_float(row.get("AMOUNT OF EXEMPT SALES"))
                zero_rated = _to_float(row.get("AMOUNT OF ZERO RATED SALES"))
                vatable = (_to_float(row.get("AMOUNT OF TAXABLE SALES - PRIVATE"))
                        + _to_float(row.get("AMOUNT OF TAXABLE SALES - GOVERNMENT")))
                vat_amount = _to_float(row.get("AMOUNT OF OUTPUT TAX"))

                total_exempt += exempt
                total_zero_rated += zero_rated
                total_vatable += vatable
                total_vat += vat_amount

                detail_lines.append(
                    f'D,S,{customer_tin},{customer_name},,,,{region_code},{region_name},'
                    f'{exempt:.2f},{zero_rated:.2f},{vatable:.2f},{vat_amount:.2f},{company_tin},{period}'
                )

            header = (
                f'H,S,{company_tin},{company_name},,,,{trade_name},{region_code},{region_name},'
                f'{total_exempt:.2f},{total_zero_rated:.2f},{total_vatable:.2f},{total_vat:.2f},'
                f'{rdo_code},{period},{len(rows)}'
            )

            lines = [header] + detail_lines

        elif self.name == 'vat_summary_purchase':
            period = self.to_date.strftime('%m/%d/%Y')
            company_tin = agent_tin
            company_name = company.name or ''
            trade_name = company.name or ''
            company_address = company.partner_id.contact_address_complete or ''
            rdo_code = company.rdo_code or ''
            fiscal_year_end = '12'  # calendar-year default; no field for this on res.company yet

            total_exempt = 0.0
            total_zero_rated = 0.0
            total_service = 0.0
            total_capital = 0.0
            total_other_goods = 0.0
            total_vat = 0.0
            detail_lines = []

            for row in rows:
                supplier_tin = (row.get("TAX PAYER IDENTIFICATION NUMBER") or '').replace('-', '')
                supplier_name = row.get("REGISTERED NAME") or ''
                supplier_address = row.get("SUPPLIER ADDRESS") or ''  # not split into street/city upstream

                exempt = _to_float(row.get("AMOUNT OF EXEMPT PURCHASE"))
                zero_rated = _to_float(row.get("AMOUNT OF ZERO-RATED PURCHASE"))
                service = _to_float(row.get("AMOUNT OF PURCHASE OF SERVICES"))
                capital = _to_float(row.get("AMOUNT OF PURCHASE OF CAPITAL GOODS"))
                other_goods = _to_float(row.get("AMOUNT OF PURCHASE OF GOODS OTHER THAN CAPITAL GOODS"))
                vat_amount = _to_float(row.get("AMOUNT OF INPUT TAX"))

                total_exempt += exempt
                total_zero_rated += zero_rated
                total_service += service
                total_capital += capital
                total_other_goods += other_goods
                total_vat += vat_amount

                detail_lines.append(
                    f'D,P,{supplier_tin},{supplier_name},,,,{supplier_address},,'
                    f'{exempt:.2f},{zero_rated:.2f},{service:.2f},{capital:.2f},{other_goods:.2f},'
                    f'{vat_amount:.2f},{company_tin},{period}'
                )

            header = (
                f'H,P,{company_tin},{company_name},,,,{trade_name},{company_address},,'
                f'{total_exempt:.2f},{total_zero_rated:.2f},{total_service:.2f},{total_capital:.2f},'
                f'{total_other_goods:.2f},{total_vat:.2f},{total_vat:.2f},0.00,'
                f'{rdo_code},{period},{fiscal_year_end}'
            )

            lines = [header] + detail_lines
            
        dat_content = "\n".join(lines)
        attachment = self.env['ir.attachment'].create({
            'name': f"{self.name}.dat",
            'type': 'binary',
            'datas': base64.b64encode(dat_content.encode('utf-8')),
            'res_model': self._name,
            'res_id': self.id,
            'mimetype': 'text/plain',
        })
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