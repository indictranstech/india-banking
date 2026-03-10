// Copyright (c) 2026, Aerele Technologies Private Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on('Bank Payment Approval Settings', {
	refresh: function(frm) {

		frappe.db.get_list('Party Type', {
			pluck: 'name',
			limit: 0
		}).then(function(party_type_list) {

			party_type_list.push("Development Apprentice Master");
			frm.set_query("party_type", "party_type_exceptions", function(doc, cdt, cdn) {
				return {
					filters: {
						name: ["in", party_type_list]
					}
				};
			});

		});

	}
});

frappe.ui.form.on("Payment Approval Stages", {

	payment_approval_stages_add: function(frm, cdt, cdn) {

		let row = frappe.get_doc(cdt, cdn);

		row.approver_level = row.idx;

		if (row.idx > 1){
			let prev_row = frm.doc.payment_approval_stages[row.idx - 2];
			if (!prev_row.to_amount){
				frappe.msgprint("Please set To Amount in previous row first");
				return;
			}
			row.from_amount = prev_row.to_amount + 1;
		}

		frm.refresh_field("payment_approval_stages");
	}

	});