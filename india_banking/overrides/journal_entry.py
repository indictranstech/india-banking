import frappe
from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import (
	get_accounting_dimensions,
)
from frappe import _
from frappe.query_builder import DocType
from frappe.utils import get_link_to_form
from pypika.terms import ExistsCriterion
from india_banking.overrides.payment_order import get_party_summary
from india_banking.india_banking.doctype.bank_connector.bank_connector import make_payment
from frappe.utils import get_link_to_form
from frappe.model.mapper import get_mapped_doc
import json


@frappe.whitelist()
def make_payment_order(source_name, target_doc=None, args=None):

	def validate_party_bank_account(party_details, party_bank_details, invalid_party_details):
		for party_detail in party_details:
			party_type, party = party_detail.values()
			msg = ""

			if (party_type, party) in party_bank_details:
				continue

			bank_account = None

			# If Supplier → fetch from Paty Bank Account
			if party_type in ["Supplier", "Employee", "Development Apprentice Master"]:
				if party_type == "Employee":
					custom_apprentice  = frappe.db.get_value("Employee", party, "custom_apprentice")
					if frappe.db.get_value("Employee", party, "custom_apprentice"):
						party_type = "Development Apprentice Master"
    
				bank_account = frappe.get_value(
					"Party Bank Account",
					{
						"party_type": party_type,
						"party" : party
					},
					["name", "disabled", "is_default"],
					as_dict=1,
				)

				doctype = "Party Bank Account"

			# For other party types → fetch from Bank Account
			else:
				bank_account = frappe.get_value(
					"Bank Account",
					{
						"party_type": party_type,
						"party": party,
					},
					["name", "disabled", "is_default"],
					as_dict=1,
				)

				doctype = "Bank Account"
			# Validations
			if not bank_account:
				msg += f"<b>{party_type}-{party}</b> does not have a bank account.<br>"

			if bank_account and not bank_account.is_default:
				msg += f"<b>{party_type}-{party}</b> has no default bank account.<br>"

			if bank_account and bank_account.disabled:
				bank_account_link = get_link_to_form(doctype, bank_account.name)
				msg += (
					f"<b>{party_type}-{party}</b> bank account "
					f"{bank_account_link} is disabled.<br>"
				)

			#  Collect invalid details
			if msg:
				if msg not in invalid_party_details:
					invalid_party_details.append(msg)
			else:
				if party_type == "Development Apprentice Master":
					party_type = "Employee"
				party_bank_details.update(
					{(party_type, party): bank_account.name}
				)


	def update_bank_entry(source, target):
		net_payable = 0
		net_receivable = 0
		party_receivables = {}
		invalid_party_details = []
		party_bank_details = {}
		if source.accounts:
			party_details = [
				{"party_type": acc.party_type, "party": acc.party}
				for acc in source.accounts
				if acc.party_type and acc.party
			]
			
			validate_party_bank_account(
				party_details, party_bank_details, invalid_party_details
			)
			if invalid_party_details:
				if msg := "".join(invalid_party_details):
					journal_entry_link = get_link_to_form("Journal Entry", source.name)
					frappe.msgprint(
						_(
							(
								"We can see Some bank entries are missing bank account details and have been ignored."
								"Please update the bank account information and try again."
								f"</br></br><p style='color:red'><b>The missing details for {journal_entry_link} are provided below.</b></p>"
							)
							+ msg
						),
						title=_("Missing Bank Account"),
						indicator="orange",
					)
					return

			for acc in source.accounts:
				if acc.account == target.account:
					net_payable += (
						acc.debit_in_account_currency - acc.credit_in_account_currency
					)
				else:
					if acc.party_type and acc.party:
						key = (acc.party_type, acc.party)
						if key in party_receivables:
							net_receivable += (
								acc.debit_in_account_currency
								- acc.credit_in_account_currency
							)
							receivables = (
								acc.debit_in_account_currency
								- acc.credit_in_account_currency
							)
							net_receivable += receivables
							party_receivables[key].payable_amount += receivables
						else:
							party_receivables[key] = acc
							receivables = (
								acc.debit_in_account_currency
								- acc.credit_in_account_currency
							)
							net_receivable += receivables
							party_receivables[key].payable_amount = receivables
			amount = net_payable + net_receivable
			if amount > 0:
				entry_link = get_link_to_form("Journal Entry", source.name)
				frappe.msgprint(
					_(
						f"Bank Entry {frappe.bold(entry_link)} is ambiguous and will be ignored."
					)
				)
				return

		ordered_bank_entries = frappe.get_all(
			"Payment Order Reference",
			filters={
				"docstatus": ["in", [0, 1]],
				"reference_doctype": "Journal Entry",
				"parent": ["!=", target.name],
			},
			fields=["reference_doctype", "reference_name", "journal_entry_account"],
			order_by="idx",
			as_list=True,
		)

		already_fetched = [
			(
				reference.reference_doctype,
				reference.reference_name,
				reference.journal_entry_account,
			)
			for reference in target.references
		]
		journal_accounts = []

		for entry in party_receivables.values():
			if entry.payable_amount > 0:
				if (entry.parenttype, entry.parent, entry.name) in ordered_bank_entries:
					continue
				if (entry.parenttype, entry.parent, entry.name) in already_fetched:
					continue
				entry.payment_amount = entry.payable_amount
				entry.party_bank_account = party_bank_details[
					(entry.party_type, entry.party)
				]
				if entry.party_bank_account:
					journal_accounts.append(entry)

		target.payment_order_type = "Journal Entry"
		target.docstaus = 0
		target.status = "Pending"

		def _update_dimensions(source):
			return {
				dimension: source.get(dimension, "")
				for dimension in get_accounting_dimensions()
			}

		for journal_account in journal_accounts:
			details = {
				"reference_doctype": "Journal Entry",
				"reference_name": journal_account.parent,
				"journal_entry_account": journal_account.name,
				"amount": journal_account.payment_amount,
				# "party_type": journal_account.party_type,
				"party": journal_account.party,
				"mode_of_payment": "",
				# "bank_account": journal_account.party_bank_account,
				"account": journal_account.account,
				"project": journal_account.project,
				"cost_center": journal_account.cost_center,
			}
			if journal_account.party_type in ["Supplier", "Employee"]:
				details["custom_supplier_bank_account"] = journal_account.party_bank_account
			else:
				details["bank_account"] = journal_account.party_bank_account

			if journal_account.party_type == "Employee":
				custom_apprentice  = frappe.db.get_value("Employee", journal_account.party, "custom_apprentice")
				if frappe.db.get_value("Employee", journal_account.party, "custom_apprentice"):
					details["party_type"] = "Development Apprentice Master"
				else:
					details["party_type"] = "Employee"
			else:
				details["party_type"] = journal_account.party_type
			
			details.update(_update_dimensions(journal_account))

			target.append("references", details)

	doclist = get_mapped_doc(
		"Journal Entry",
		source_name,
		{
			"Journal Entry": {
				"doctype": "Payment Order",
			}
		},
		target_doc,
		update_bank_entry,
	)

	return doclist


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_bank_entry(doctype, txt, searchfield, start, page_len, filters, as_dict):
	filters = frappe._dict(filters)

	JournalEntry = DocType("Journal Entry")
	JournalEntryAccount = DocType("Journal Entry Account")

	ordered_bank_entries = frappe.get_all(
		"Payment Order Reference",
		filters={
			"docstatus": ["in", [0, 1]],
			"reference_doctype": "Journal Entry",
		},
		pluck="reference_name",
	)

	query = (
		frappe.qb.from_(JournalEntry)
		.join(JournalEntryAccount)
		.on(JournalEntry.name == JournalEntryAccount.parent)
		.select(
			JournalEntryAccount.parent.as_("name"),
			JournalEntry.company,
			JournalEntry.voucher_type,
		)
		.where(
			(JournalEntry.docstatus == 1)
			& (JournalEntry.voucher_type.eq("Bank Entry"))
			& (
				ExistsCriterion(
					frappe.qb.from_(JournalEntryAccount)
					.select("name")
					.where(
						(JournalEntryAccount.parent == JournalEntry.name)
						& (JournalEntryAccount.account == filters.company_account)
					)
				)
			)
			& (
				ExistsCriterion(
					frappe.qb.from_(JournalEntryAccount)
					.select("name")
					.where(
						(JournalEntryAccount.parent == JournalEntry.name)
						& (JournalEntryAccount.account != filters.company_account)
						& (JournalEntryAccount.party_type.isnotnull())
						& (
							JournalEntryAccount.payment_status.notin(
								["Ordered", "Payment Ordered", "Paid"]
							)
						)
					)
				)
			)
		)
		.groupby(JournalEntryAccount.parent)
	)

	if searchfield:
		if searchfield == "name":
			query = query.where(JournalEntry.name.like(f"%{txt}%"))
	if ordered_bank_entries:
		query = query.where(JournalEntry.name.notin(ordered_bank_entries))

	return query.run(as_dict=as_dict)





def on_submit(doc, method):
    if doc.voucher_type == "Bank Entry" and doc.custom_bank_entry_type == "H2H":
        auto_payment_order_on_submit(doc)


#new method call on submit of jv
def auto_payment_order_on_submit(self):

	# Find Credit Bank Account
	bank_account = None

	for row in self.accounts:
		if row.credit and row.credit > 0 and not row.party_type and not row.party:
			bank_account = row.bank_account
			break

	if not bank_account:
		frappe.log_error(f"No credit Bank Account found in JV {self.name}",
						"Auto Payment Order JV")
		return

	try:
		# Get Bank Details
		bank_details = frappe.db.get_value("Bank Account", bank_account, ["account", "company", "bank"], as_dict=True)

		if not bank_details:
			frappe.log_error(
				f"Bank Account not found: {bank_account}",
				"Auto Payment Order JV")
			return

		# Prevent Duplicate Payment Order
		existing_po = frappe.db.exists("Payment Order Reference",
			{
				"reference_name": self.name,
				"reference_doctype": "Journal Entry"
			}
		)

		if existing_po:
			frappe.msgprint(
				f"Payment Order already exists for JV {self.name}"
			)
			frappe.log_error(
					f"Payment Order already exists for JV {self.name}",
					"Auto Payment Order JV")
			return

		# Create Payment Order
		payment_order = frappe.new_doc("Payment Order")
		payment_order.company = bank_details.company
		payment_order.company_bank_account = bank_account
		payment_order.account = bank_details.account
		payment_order.bank = bank_details.bank

		set_deafult_mode_of_transfer(payment_order, sum_level=0)

		# # Validate Party Bank Accounts
		# validate_bank_account(self.name, from_scheduler=1)

		# Add Reference
		payment_order = make_payment_order( self.name,
											target_doc=payment_order
										)

		if not payment_order.references:
			frappe.log_error(
				f"No references created for JV {self.name}",
				"Auto Payment Order JV"
			)
			return

		# Generate Summary

		references = json.dumps(
			[d.as_dict() for d in payment_order.references]
		)

		summary_data = get_party_summary(
											references,
											bank_account,
											payment_order.summarise_payment_based_on,
										)

		if not summary_data:
			frappe.log_error(
				f"No summary generated for JV {self.name}",
				"Auto Payment Order JV"
			)
			return

		# Fill Summary Table
		payment_order.set("summary", [])
		payment_order.total = 0

		for item in summary_data:
			row = payment_order.append("summary", item)
			payment_order.total += item.get("amount", 0)

			if not row.mode_of_transfer:
				set_deafult_mode_of_transfer(row, sum_level=1)

		# Save & Submit
		payment_order.insert(ignore_permissions=True)
		payment_order.submit()

		frappe.msgprint(
			f"Payment Order {payment_order.name} created successfully."
		)
		frappe.db.commit()

		# Make Bank Payment (Initiate Payment)
		make_payment(payment_order.name)

	except Exception:
		frappe.log_error(
			title=f"Auto PO: Error Creating Payment Order for JV {self.name}",
			message=frappe.get_traceback()
		)
		raise

def set_deafult_mode_of_transfer(row, sum_level=None):
	default_mode = frappe.db.get_value("Mode of Transfer",{"custom_is_default": 1},"mode")

	if not default_mode:
		# frappe.throw("Please set a default Mode of Transfer")
		frappe.log_error(
					f"Please set a default Mode of Transfer",
					"Auto Payment Order JV"
				)

	if sum_level == 1:
		row.mode_of_transfer = default_mode
	else:
		row.default_mode_of_transfer = default_mode


def validate(doc, method):
	if doc.voucher_type != "Bank Entry" and doc.custom_bank_entry_type != "H2H":
		return
	
	validate_party_details(doc)
	validate_bank_account(doc, from_scheduler =0)
	validate_workflow_approval(doc)

def validate_bank_account(doc ,from_scheduler = 0):
	invalid_party_details = []
	party_bank_details = {}

	if from_scheduler:
		doc = frappe.get_doc("Journal Entry",doc)

	for party_detail in doc.accounts:
		party_type = party_detail.party_type
		party = party_detail.party

		# party_type, party = party_detail.values()
		msg = ""

		if not party_type or not party:
			continue

		if (party_type, party) in party_bank_details:
			continue

		bank_account = None

		# If Supplier → fetch from Paty Bank Account
		if party_type in ["Supplier", "Employee", "Development Apprentice Master"]:
			if party_type == "Employee":
				custom_apprentice  = frappe.db.get_value("Employee", party, "custom_apprentice")
				if frappe.db.get_value("Employee", party, "custom_apprentice"):
					party_type = "Development Apprentice Master"

			bank_account = frappe.get_value(
				"Party Bank Account",
				{
					"party_type": party_type,
					"party" : party
				},
				["name", "disabled", "is_default"],
				as_dict=1,
			)

			doctype = "Party Bank Account"

		# For other party types → fetch from Bank Account
		else:
			bank_account = frappe.get_value(
				"Bank Account",
				{
					"party_type": party_type,
					"party": party,
				},
				["name", "disabled", "is_default"],
				as_dict=1,
			)

			doctype = "Bank Account"

		# Validations
		if not bank_account:
			msg += f"<b>{party_type}-{party}</b> does not have a {doctype}.<br>"

		if bank_account and not bank_account.is_default:
			msg += f"<b>{party_type}-{party}</b> has no default {doctype}.<br>"

		if bank_account and bank_account.disabled:
			bank_account_link = get_link_to_form(doctype, bank_account.name)
			msg += (
				f"<b>{party_type}-{party}</b> {doctype} "
				f"{bank_account_link} is disabled.<br>"
			)

		#  Collect invalid details
		if msg:
			if msg not in invalid_party_details:
				invalid_party_details.append(msg)

		else:
			if party_type == "Development Apprentice Master":
				party_type = "Employee"
			party_bank_details.update(
				{(party_type, party): bank_account.name}
			)

	if invalid_party_details:
		final_msg = "".join(invalid_party_details)
		if from_scheduler:
			frappe.log_error(
				title=f"Auto PO: Bank Account Validation Failed for JV {doc.name}",
				message=final_msg
			)
		else:
			frappe.throw(final_msg)



def validate_party_details(doc):
	level_list = []
	party_set = set()
	party_type_set = set()

	for entry in doc.accounts:
		if entry.party_type and entry.party:

			party_type = entry.party_type
			if entry.party_type == "Development Apprentice Master":
				party_type = "Employee"

			party_type_set.add(entry.custom_party_type)

			# duplicate check
			party_key = (entry.party_type, entry.party)
			if party_key in party_set:
				frappe.throw(
					f"Row {entry.idx}: Same Party and Party Type is not allowed multiple times in Accounting Entries."
				)

			party_set.add(party_key)

			no_of_levels = get_no_of_levels_for_range(entry.debit)
			level_list.append(no_of_levels)
	# check different party types in same entry
	if len(party_type_set) > 1:
		frappe.throw("Multiple Party Types are not allowed in one Bank Entry.")

	# check multiple ranges
	if len(set(level_list)) > 1:
		frappe.throw("Not Allow multiple Debit amount ranges in one Bank Entry.")


def validate_workflow_approval(doc):
	if doc.workflow_state == "Pending" and doc.custom_current_approval_state == 0:
		doc.custom_current_approval_state = 1

	level_dict = get_level_data_and_set_no_of_states(doc)
	if doc.workflow_state not in ['Draft', 'Pending']:
		workflow_state_changes(doc, level_dict)



def get_approval_leves_from_paymnet_setting(debit, party_type):
	approval_levels = 0
    # Get Bank Payment Approval Settings (Single Doctype)
	settings = frappe.get_single("Bank Payment Approval Settings")

    # Check Party Type Exceptions
	for row in settings.party_type_exceptions:
		if row.party_type == party_type:
			return row.no_of_approval_levels

    # Check Payment Approval Stages
	for row in settings.payment_approval_stages:

		from_amount = row.from_amount or 0
		to_amount = row.to_amount or 0

		# Case: No upper limit
		if to_amount == 0:
			if debit >= from_amount:
				approval_levels = max(approval_levels, row.approver_level)

		else:
			if from_amount <= debit <= to_amount:
				approval_levels = max(approval_levels, row.approver_level)

	return approval_levels


def get_level_data_and_set_no_of_states(doc):

	level_dict = {}
	all_level = []
	no_of_levels = 0
	max_debit = max((row.debit for row in doc.accounts if row.debit and row.debit > 0),default=0)
	debit_rows = [d for d in doc.accounts if d.debit and d.debit > 0]
	if debit_rows:
		max_row = max(debit_rows, key=lambda x: x.debit)

		max_debit = max_row.debit
		party_type = max_row.party_type
		party = max_row.party
	if max_debit:
		no_of_levels = get_approval_leves_from_paymnet_setting(max_debit, party_type)


	if no_of_levels > 0:
		all_level = frappe.db.sql("""
									SELECT approver_level, approver_role
									FROM `tabPayment Approval Stages`
									WHERE approver_level <= %s
									order by approver_level asc
								""", (no_of_levels,), as_dict=True)


		for level in all_level:
			level_dict[level.approver_level] = {
												"approver_role": level.approver_role
												}

	max_level = max(level_dict.keys()) if level_dict else 0

	# if not doc.custom_no_of_states:
	doc.custom_no_of_states = max_level
	return level_dict

def workflow_state_changes(doc, level_dict):

	cur_state = doc.custom_current_approval_state
	user_roles = frappe.get_roles()

	# stop if current Approval State is reached to no. of states
	if cur_state > doc.custom_no_of_states:
		return

	level_data = level_dict.get(cur_state) 
	if not level_data:
		frappe.throw(f"Approval configuration missing for level {cur_state} in Bank Payment Approval Settings")

	approver_role = level_data.get("approver_role")

	# validate role
	if approver_role not in user_roles:
		frappe.throw(f"User must have role: {approver_role}")

	# update workflow and add 1 in current approval state
	# doc.workflow_state = approved_state
	if cur_state < doc.custom_no_of_states:
		doc.custom_current_approval_state = cur_state + 1

def get_no_of_levels_for_range(total_debit):
    return frappe.db.sql("""
        SELECT COALESCE(MAX(approver_level), 0)
        FROM `tabPayment Approval Stages`
        WHERE from_amount <= %(amount)s
        AND (
            to_amount >= %(amount)s
            OR IFNULL(to_amount, 0) = 0
        )
    """, {"amount": total_debit})[0][0]