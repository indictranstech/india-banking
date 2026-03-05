# Copyright (c) 2026, Aerele Technologies Private Limited and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

class BankPaymentApprovalSettings(Document):
	def validate(doc, method=None):

		if doc.max_no_of_approvers > 5:
			frappe.throw("Maximum number of approvers cannot be greater than 5")

		validate_payment_Approval_stages(doc)
		validate_party_type_exceptions(doc)


def validate_payment_Approval_stages(doc):
	# max_from_amount = max_level_from_amount(doc)

	for stage in doc.payment_approval_stages or []:
		if stage.approver_level and stage.approver_level > doc.max_no_of_approvers:
			frappe.throw(
				f"Approver Level: ({stage.approver_level}) cannot be greater than Maximum No of Approvers: ({doc.max_no_of_approvers})"
			)

		if doc.max_no_of_approvers != stage.approver_level and not stage.to_amount:
			frappe.throw(
				f"Row {stage.idx}: To Amount is mandatory.")


def validate_party_type_exceptions(doc):
	max_level = max([row.approver_level for row in doc.payment_approval_stages],default=0)
	for row in doc.party_type_exceptions or []:
		if (row.no_of_approval_levels > doc.max_no_of_approvers) or (row.no_of_approval_levels > max_level):
			frappe.throw(
				f"Row {row.idx}: No of Approval Level cannot be greater than Maximum No of Approvers or Approval stages."
			)


def max_level_from_amount(doc):
	max_from_amount = None

	#from_amount of last max approver level
	for stage in doc.payment_approval_stages or []:
		if stage.approver_level == doc.max_no_of_approvers:
			max_from_amount = stage.from_amount
			break
	return max_from_amount
