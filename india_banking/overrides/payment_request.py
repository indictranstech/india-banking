import frappe
from erpnext import get_company_currency
from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import (
	get_accounting_dimensions,
)
from erpnext.accounts.doctype.payment_request.payment_request import (
	PaymentRequest,
)
# from erpnext.accounts.doctype.tax_withholding_category.tax_withholding_category import (
# 	get_party_tax_withholding_details,
# )
from erpnext.accounts.party import (
	get_party_account,
	get_party_account_currency,
	# get_party_bank_account,
)
from india_banking.utils import get_party_bank_account
from frappe import _, bold
from frappe.utils import get_link_to_form, getdate, cint

from india_banking.utils import validate_party_bank_account_details


class BankPaymentRequest(PaymentRequest):
	def update_party_account_currency(self):
		if self.is_adhoc:
			self.party_account_currency = get_party_account_currency(
				self.party_type, self.party, self.company
			)

	def validate(self):
		set_supplier_bank_details(self)
		if not self.net_total:
			self.net_total = self.grand_total

		if self.payment_request_type != "Outward":
			super().validate()
			return

		self.set_default_value()

		if (
			self.apply_tax_withholding_amount
			and self.tax_withholding_category
			and self.is_adhoc
		):
			tds_amount = self.calculate_pr_tds(self.net_total)
			self.taxes_deducted = tds_amount
			self.grand_total = self.net_total - self.taxes_deducted
		else:
			self.grand_total = self.net_total or 0

		if not self.is_adhoc:
			super().validate()
		else:
			self.update_party_account_currency()
			if self.is_new():
				self.status = "Draft"
			if self.reference_doctype or self.reference_name:
				frappe.throw(_("Payments with references cannot be marked as ad-hoc"))

		if self.remarks:
			self.remarks = self.remarks[:48]

	def set_default_value(self):
		if not self.transaction_date:
			self.transaction_date = getdate()

		if not self.payment_type:
			if payment_type := frappe.db.exists(
				"Payment Type",
				{
					"company": self.company,
					"is_default": 1,
				},
			):
				self.payment_type = payment_type

		if not self.bank_account:
			filters = {
				"party_type": self.party_type,
				"party": self.party,
				"is_default": 1,
				"disabled": 0,
			}
			if bank_account := frappe.get_value("Bank Account", filters, "name"):
				frappe.msgprint(
					"The default bank account is set to {}".format(
						frappe.bold(bank_account)
					)
				)
				self.bank_account = bank_account

		if self.bank_account:
			self.update({**self.get_bank_account_details()})

		if self.bank_account:
			self.mode_of_payment = "Wire Transfer"

	def get_bank_account_details(self):
		if self.bank_account:
			return (
				frappe.get_value(
					"Bank Account",
					self.bank_account,
					["bank", "bank_account_no", "branch_code", "iban"],
					as_dict=1,
				)
				or {}
			)

	def on_submit(self):
		super().on_submit()

		if self.payment_request_type != "Outward":
			return

		if self.is_adhoc:
			self.db_set("status", "Initiated")

		if not self.grand_total or not self.net_total:
			frappe.throw(_("Amount cannot be zero"))

		# self.validate_bank_account()
		self.custom_validate_bank_account()
		self.validate_currency()

	def validate_currency(self):
		if self.payment_request_type != "Outward":
			super().validate_currency()
			return
		currency_field = (
			"salary_currency" if self.party_type == "Employee" else "default_currency"
		)
		transaction_currency = frappe.get_value(
			self.party_type, self.party, currency_field
		) or get_company_currency(self.company)

		if transaction_currency != self.currency:
			frappe.throw(f"Transaction currency must be in {transaction_currency}")

		party_account_currency = get_party_account_currency(
			self.party_type, self.party, self.company
		)
		if party_account_currency != self.party_account_currency:
			frappe.throw(
				f"Party account currency should be in {party_account_currency}"
			)

	def validate_bank_account(self):
		if not self.bank_account:
			if validate_party_bank_account_details(self, update=True):
				return

		bank_account = get_party_bank_account(self.party_type, self.party)
		if not self.bank_account:
			if not bank_account:
				frappe.throw(
					_(
						"Default Bank Account is missing for {0} - {1}".format(
							self.party_type, frappe.bold(self.party)
						)
					)
				)
			else:
				self.bank_account = bank_account

		bank_account = frappe.get_doc("Bank Account", self.bank_account)
		if frappe.db.get_single_value(
			"India Banking Settings", "activate_workflow_on_bank_account"
		):
			if bank_account.workflow_state != "Approved":
				frappe.throw(
					title=_("Cannot proceed with un-approved bank account"),
					msg=_(
						"{}-{}- Bank Account {}".format(
							self.party_type,
							self.party,
							get_link_to_form("Bank Account", bank_account.name),
						)
					),
				)

		if bank_account.currency != self.currency:
			frappe.throw(
				title="Invalid currency",
				msg=_(
					f"The party bank account currency ({bold(bank_account.currency)})  and the transaction currency ({bold(self.currency)}) cannot be different. Please select a matching currency."
				),
			)

		if self.bank_account:
			bank_account_company = frappe.db.get_value(
				"Bank Account", self.bank_account, "company"
			)
			if self.company != bank_account_company:
				frappe.throw(
					_(
						"Bank Account <b>{0}</b> is not valid for company <b>{1}</b>".format(
							self.bank_account, self.company
						)
					)
				)

	def custom_validate_bank_account(self):
		# if self.party_type == "Supplier":
		if self.party_type in ["Supplier", "Employee"]:
			# if not self.bank_account:
			# 	if validate_party_bank_account_details(self, update=True):
			# 		return

			# supplier_bank_account = get_party_bank_account(self.party_type, self.party)
			supplier_bank_account = frappe.db.get_value("Party Bank Account", {"party": self.party, "party_type": self.party_type, "is_default": 1, "docstatus":1, "disabled": 0})
			# print("\n\n*********************** supplier_bank_account: ",supplier_bank_account)
			if not self.custom_supplier_bank_account:
				if not supplier_bank_account:
					frappe.throw(
						_(
							"Default Party Bank Account is missing for {0} - {1}".format(
								self.party_type, frappe.bold(self.party)
							)
						)
					)
				else:
					self.custom_supplier_bank_account = supplier_bank_account

			supplier_bank_account = frappe.get_doc("Party Bank Account", self.custom_supplier_bank_account)
			if frappe.db.get_single_value(
				"India Banking Settings", "activate_workflow_on_bank_account"
			):
				if supplier_bank_account.workflow_state != "Approved":
					frappe.throw(
						title=_("Cannot proceed with un-approved bank account"),
						msg=_(
							"{}-{}- Party Bank Account {}".format(
								self.party_type,
								self.party,
								get_link_to_form("Party Bank Account", supplier_bank_account.name),
							)
						),
					)

			if supplier_bank_account.currency != self.currency:
				frappe.throw(
					title="Invalid currency",
					msg=_(
						f"The party Party bank account currency ({bold(supplier_bank_account.currency)})  and the transaction currency ({bold(self.currency)}) cannot be different. Please select a matching currency."
					),
				)

		else:

			if not self.bank_account:
				if validate_party_bank_account_details(self, update=True):
					return

			bank_account = get_party_bank_account(self.party_type, self.party)
			if not self.bank_account:
				if not bank_account:
					frappe.throw(
						_(
							"Default Bank Account is missing for {0} - {1}".format(
								self.party_type, frappe.bold(self.party)
							)
						)
					)
				else:
					self.bank_account = bank_account

			bank_account = frappe.get_doc("Bank Account", self.bank_account)
			if frappe.db.get_single_value(
				"India Banking Settings", "activate_workflow_on_bank_account"
			):
				if bank_account.workflow_state != "Approved":
					frappe.throw(
						title=_("Cannot proceed with un-approved bank account"),
						msg=_(
							"{}-{}- Bank Account {}".format(
								self.party_type,
								self.party,
								get_link_to_form("Bank Account", bank_account.name),
							)
						),
					)

			if bank_account.currency != self.currency:
				frappe.throw(
					title="Invalid currency",
					msg=_(
						f"The party bank account currency ({bold(bank_account.currency)})  and the transaction currency ({bold(self.currency)}) cannot be different. Please select a matching currency."
					),
				)

			if self.bank_account:
				bank_account_company = frappe.db.get_value(
					"Bank Account", self.bank_account, "company"
				)
				if self.company != bank_account_company:
					frappe.throw(
						_(
							"Bank Account <b>{0}</b> is not valid for company <b>{1}</b>".format(
								self.bank_account, self.company
							)
						)
					)

	def calculate_pr_tds(self, amount):
		doc = self
		doc.supplier = self.party
		doc.company = self.company
		doc.base_tax_withholding_net_total = amount
		doc.tax_withholding_net_total = amount
		doc.taxes = []
		taxes = get_party_tax_withholding_details(doc, self.tax_withholding_category)
		if taxes:
			return taxes["tax_amount"]
		else:
			return 0


@frappe.whitelist()
def make_payment_order(source_name, target_doc=None):
	# print("\n\n ^^^^^^^^^^^^^^^^^^ customm  calling from ovrride make_payment_order ")
	from frappe.model.mapper import get_mapped_doc

	def set_missing_values(source, target):
		target.payment_order_type = "Payment Request"
		account = get_party_account(source.party_type, source.party, source.company)

		def _update_dimensions(source):
			return {
				dimension: source.get(dimension, "")
				for dimension in get_accounting_dimensions()
			}

		reference = {
			"reference_doctype": source.reference_doctype,
			"reference_name": source.reference_name,
			"amount": source.grand_total,
			"party_type": source.party_type,
			"party": source.party,
			"payment_request": source_name,
			"mode_of_payment": source.mode_of_payment,
			"bank_account": source.bank_account,
			"account": account,
			"is_adhoc": source.is_adhoc,
			"cost_center": source.cost_center,
			"project": source.project,
			"tax_withholding_category": source.tax_withholding_category,
			"custom_supplier_bank_account": source.custom_supplier_bank_account,
			"bank": source.bank,
			"bank_account_no": source.bank_account_no,
			"branch_code": source.branch_code,
			"account_name": source.bank_account_no,
		}
		reference.update(_update_dimensions(source))

		target.append(
			"references",
			reference,
		)
		target.status = "Pending"

	doclist = get_mapped_doc(
		"Payment Request",
		source_name,
		{
			"Payment Request": {
				"doctype": "Payment Order",
			}
		},
		target_doc,
		set_missing_values,
	)

	return doclist

def set_supplier_bank_details(self, method=None):

	# Only for Supplier
	if self.party_type not in ["Supplier", "Employee"]:
		return

	# Get default Party bank account details
	supplier_bank_details = frappe.get_value("Party Bank Account", {"party": self.party, "party_type": self.party_type, "is_default": 1, "docstatus": 1, "disabled": 0},
		["name", "bank_name", "account_number", "iban", "ifsc_code"], as_dict=True)

	# print("\n\n\n\n ---------------------supplier_bank:",supplier_bank)
	if not supplier_bank_details:
		frappe.throw(
			_("Please set a Default Party Bank Account for {0} '{1}'.")
			.format(self.party_type, self.party)
		)

	# Set bank details from Party Bank Account
	self.custom_supplier_bank_account = supplier_bank_details.name
	self.bank = supplier_bank_details.bank_name
	self.bank_account_no = supplier_bank_details.account_number
	self.branch_code = supplier_bank_details.ifsc_code
	self.iban = supplier_bank_details.iban

def get_party_tax_withholding_details(inv, tax_withholding_category=None):
	if inv.doctype == "Payment Entry":
		inv.tax_withholding_net_total = inv.net_total

	pan_no = ""
	parties = []
	party_type, party = get_party_details(inv)
	has_pan_field = frappe.get_meta(party_type).has_field("pan")

	if not tax_withholding_category:
		if has_pan_field:
			fields = ["tax_withholding_category", "pan"]
		else:
			fields = ["tax_withholding_category"]

		tax_withholding_details = frappe.db.get_value(party_type, party, fields, as_dict=1)

		tax_withholding_category = tax_withholding_details.get("tax_withholding_category")
		pan_no = tax_withholding_details.get("pan")

	if not tax_withholding_category:
		return

	# if tax_withholding_category passed as an argument but not pan_no
	if not pan_no and has_pan_field:
		pan_no = frappe.db.get_value(party_type, party, "pan")

	# Get others suppliers with the same PAN No
	if pan_no:
		parties = frappe.get_all(party_type, filters={"pan": pan_no}, pluck="name")

	if not parties:
		parties.append(party)

	posting_date = inv.get("posting_date") or inv.get("transaction_date")
	tax_details = get_tax_withholding_details(tax_withholding_category, posting_date, inv.company)

	if not tax_details:
		frappe.throw(
			_("Please set associated account in Tax Withholding Category {0} against Company {1}").format(
				tax_withholding_category, inv.company
			)
		)

	if party_type == "Customer" and not tax_details.cumulative_threshold:
		# TCS is only chargeable on sum of invoiced value
		frappe.throw(
			_(
				"Tax Withholding Category {} against Company {} for Customer {} should have Cumulative Threshold value."
			).format(tax_withholding_category, inv.company, party)
		)

	tax_amount, tax_deducted, tax_deducted_on_advances, voucher_wise_amount = get_tax_amount(
		party_type, parties, inv, tax_details, posting_date, pan_no
	)

	if party_type == "Supplier":
		tax_row = get_tax_row_for_tds(tax_details, tax_amount)
	else:
		tax_row = get_tax_row_for_tcs(inv, tax_details, tax_amount, tax_deducted)

	cost_center = get_cost_center(inv)
	tax_row.update({"cost_center": cost_center})

	if cint(tax_details.round_off_tax_amount):
		inv.round_off_applicable_accounts_for_tax_withholding = tax_details.account_head

	if inv.doctype == "Purchase Invoice":
		return tax_row, tax_deducted_on_advances, voucher_wise_amount
	else:
		return tax_row

def get_party_details(inv):
	party_type, party = "", ""

	if inv.doctype == "Sales Invoice":
		party_type = "Customer"
		party = inv.customer
	else:
		party_type = "Supplier"
		party = inv.supplier

	if not party:
		frappe.throw(_("Please select {0} first").format(party_type))

	return party_type, party

def get_cost_center(inv):
	cost_center = frappe.get_cached_value("Company", inv.company, "cost_center")

	if len(inv.get("taxes", [])) > 0:
		cost_center = inv.get("taxes")[0].cost_center

	return cost_center


def get_tax_withholding_details(tax_withholding_category, posting_date, company):
	tax_withholding = frappe.get_doc("Tax Withholding Category", tax_withholding_category)

	tax_rate_detail = get_tax_withholding_rates(tax_withholding, posting_date)

	for account_detail in tax_withholding.accounts:
		if company == account_detail.company:
			return frappe._dict(
				{
					"tax_withholding_category": tax_withholding_category,
					"account_head": account_detail.account,
					"rate": tax_rate_detail.tax_withholding_rate,
					"from_date": tax_rate_detail.from_date,
					"to_date": tax_rate_detail.to_date,
					"threshold": tax_rate_detail.single_threshold,
					"cumulative_threshold": tax_rate_detail.cumulative_threshold,
					"description": tax_withholding.category_name
					if tax_withholding.category_name
					else tax_withholding_category,
					"consider_party_ledger_amount": tax_withholding.consider_party_ledger_amount,
					"tax_on_excess_amount": tax_withholding.tax_on_excess_amount,
					"round_off_tax_amount": tax_withholding.round_off_tax_amount,
				}
			)

def get_tax_withholding_rates(tax_withholding, posting_date):
	# returns the row that matches with the fiscal year from posting date
	for rate in tax_withholding.rates:
		if getdate(rate.from_date) <= getdate(posting_date) <= getdate(rate.to_date):
			return rate

	frappe.throw(_("No Tax Withholding data found for the current posting date."))


def get_tax_row_for_tcs(inv, tax_details, tax_amount, tax_deducted):
	row = {
		"category": "Total",
		"charge_type": "Actual",
		"tax_amount": tax_amount,
		"description": tax_details.description,
		"account_head": tax_details.account_head,
	}

	if tax_deducted:
		# TCS already deducted on previous invoices
		# So, TCS will be calculated by 'Previous Row Total'

		taxes_excluding_tcs = [d for d in inv.taxes if d.account_head != tax_details.account_head]
		if taxes_excluding_tcs:
			# chargeable amount is the total amount after other charges are applied
			row.update(
				{
					"charge_type": "On Previous Row Total",
					"row_id": len(taxes_excluding_tcs),
					"rate": tax_details.rate,
				}
			)
		else:
			# if only TCS is to be charged, then net total is chargeable amount
			row.update({"charge_type": "On Net Total", "rate": tax_details.rate})

	return row


def get_tax_row_for_tds(tax_details, tax_amount):
	return {
		"category": "Total",
		"charge_type": "Actual",
		"tax_amount": tax_amount,
		"add_deduct_tax": "Deduct",
		"description": tax_details.description,
		"account_head": tax_details.account_head,
	}