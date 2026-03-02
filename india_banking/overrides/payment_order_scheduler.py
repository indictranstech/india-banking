import frappe
import json
from india_banking.overrides.journal_entry import get_bank_entry, make_payment_order
from india_banking.overrides.payment_order import get_party_summary
from india_banking.india_banking.doctype.bank_connector.bank_connector import make_payment
from frappe.utils import get_link_to_form

def auto_payment_order():

    frappe.enqueue(
                "india_banking.overrides.payment_order_scheduler.auto_payment_order_for_jv",
                    queue = 'long',
                    timeout = 600
                )


def auto_payment_order_for_jv():

    bank_connectors = frappe.get_all("Bank Connector", pluck="bank_account")

    if not bank_connectors:
        frappe.log_error("No Bank Connectors found", "Auto Payment Order JV")
        return

    for comp_bank_acc in bank_connectors:

        try:
            # Get Bank Details
            bank_details = frappe.db.get_value(
                "Bank Account",
                comp_bank_acc,
                ["account", "company", "bank"],
                as_dict=True
            )

            if not bank_details:
                frappe.log_error(
                    f"Bank Account not found: {comp_bank_acc}",
                    "Auto Payment Order JV"
                )
                continue

            # Get Journal Entries
            jv_entries = get_bank_entry(
                doctype="Journal Entry",
                txt="",
                searchfield="name",
                start=0,
                page_len=50,
                filters={"company_account": bank_details.account},
                as_dict=True
            )

            if not jv_entries:
                continue

            # Create new Payment Order
            payment_order = frappe.new_doc("Payment Order")
            payment_order.company = bank_details.company
            payment_order.company_bank_account = comp_bank_acc
            payment_order.account = bank_details.account
            payment_order.bank = bank_details.bank
            # payment_order.default_mode_of_transfer = "NEFT"
            set_deafult_mode_of_transfer(payment_order, sum_level=0)

            # Add References
            for entry in jv_entries:
                try:
                    validate_party_bank_account(entry.get("name"))
                    payment_order = make_payment_order(
                        entry.get("name"),
                        target_doc=payment_order
                    )
                except Exception:
                    frappe.log_error(
                        title=f"Error adding JV {entry.get('name')} to Payment Order",
                        message=frappe.get_traceback()
                    )

            if not payment_order.references:
                continue  # nothing to process

            # Get Summary
            references = json.dumps( [d.as_dict() for d in payment_order.references] )

            # summary_data = get_party_summary(
            #     references,
            #     comp_bank_acc,
            #     payment_order.summarise_payment_based_on,
            #     default_mode_of_transfer="NEFT",
            # )
            summary_data = get_party_summary(
                references,
                comp_bank_acc,
                payment_order.summarise_payment_based_on,
            )

            if not summary_data:
                continue

            # Fill Summary Table
            payment_order.set("summary", [])
            payment_order.total = 0

            for item in summary_data:
                # payment_order.append("summary", item)
                row = payment_order.append("summary", item)
                payment_order.total += item.get("amount", 0)
                if not row.mode_of_transfer:
                    set_deafult_mode_of_transfer(row, sum_level=1)

            # Save & Submit
            payment_order.insert(ignore_permissions=True)
            # payment_order.save()
            payment_order.submit()
            frappe.db.commit()

            # Make Bank Payment (Initiate Payment)
            make_payment(payment_order.name)

            # frappe.db.commit()

        except Exception:
            frappe.log_error(
                title=f"Error Creating Payment Order for Bank Connector {comp_bank_acc}",
                message=frappe.get_traceback()
            )
            frappe.db.rollback()

    # frappe.db.commit()

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

def validate_party_bank_account(entry_name):
    invalid_party_details = []
    party_bank_details = {}
    jv_doc = frappe.get_doc("Journal Entry",entry_name)

    for party_detail in jv_doc.accounts:
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

        frappe.log_error(
            title=f"Auto PO: Bank Account Validation Failed for JV {entry_name}",
            message=final_msg
        )
