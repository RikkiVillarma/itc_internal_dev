from odoo import models, fields
import base64

class BirHideFieldsMixin(models.AbstractModel):
    _name = "bir.hide.fields.mixin"
    _description = "Shared BIR toggle + PDF generation/chatter helpers"

    hide_auto_fields = fields.Boolean(
        string="Blank Form (No Fields)",
        default=True,
        help="When enabled, the generated PDF shows only the BIR form structure "
             "with all field values suppressed."
    )

    def _f(self, value):
        self.ensure_one()
        return '' if self.hide_auto_fields else (value or '')

    def _fd(self, value):
        self.ensure_one()
        return (' ' * len(value)) if self.hide_auto_fields else value

    # ── PDF generation contract — override per model ──────────────────
    def _get_pdf_filename(self):
        """Override per model. Return the attachment filename, e.g. 'BIR_0619E.pdf'."""
        self.ensure_one()
        return f"{self._name.replace('.', '_')}.pdf"

    def _generate_pdf_bytes(self):
        """Override per model — return raw PDF bytes.
        QWeb models: render via ir.actions.report._render_qweb_pdf.
        AcroForm models: build via pypdf and return buf.getvalue()."""
        raise NotImplementedError(
            f"{self._name} must implement _generate_pdf_bytes()"
        )

    # ── Shared: build PDF, replace old attachment, post to chatter ────
    def action_generate_report_chatter(self):
        self.ensure_one()
        filename = self._get_pdf_filename()
        pdf_content = self._generate_pdf_bytes()

        previous_attachments = self.env['ir.attachment'].search([
            ('res_model', '=', self._name),
            ('res_id', '=', self.id),
            ('name', '=', filename),
        ])
        if previous_attachments:
            previous_attachments.unlink()

        attachment = self.env['ir.attachment'].create({
            'name': filename,
            'type': 'binary',
            'datas': base64.b64encode(pdf_content),
            'res_model': self._name,
            'res_id': self.id,
            'mimetype': 'application/pdf',
            'store_fname': filename,
        })

        self.with_context(
            mail_notify_force_send=False,
            mail_auto_subscribe_no_notify=True
        ).message_post(
            body=f"{filename} generated.",
            message_type="comment",
            subtype_xmlid="mail.mt_log",
            attachment_ids=[attachment.id],
        )
        return attachment