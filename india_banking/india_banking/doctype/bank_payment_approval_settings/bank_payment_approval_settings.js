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

