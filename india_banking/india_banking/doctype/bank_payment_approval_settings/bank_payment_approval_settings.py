# Copyright (c) 2026, Aerele Technologies Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

class BankPaymentApprovalSettings(Document):
	def validate(doc, method=None):

		if doc.max_no_of_approvers > 5:
			frappe.throw("Maximum number of approvers cannot be greater than 5")

		for stage in doc.payment_approval_stages or []:
			if stage.approver_level and stage.approver_level > doc.max_no_of_approvers:
				frappe.throw(
					f"Approver Level: ({stage.approver_level})cannot be greater than Maximum No of Approvers: ({doc.max_no_of_approvers})"
				)
