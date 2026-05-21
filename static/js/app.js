document.addEventListener("DOMContentLoaded", () => {
    const sidebarToggle = document.querySelector("[data-sidebar-toggle]");
    const sidebarClose = document.querySelector("[data-sidebar-close]");
    function toggleSidebar(force) {
        document.body.classList.toggle("sidebar-open", force ?? !document.body.classList.contains("sidebar-open"));
    }
    sidebarToggle?.addEventListener("click", () => toggleSidebar());
    sidebarClose?.addEventListener("click", () => toggleSidebar(false));

    function numberValue(el) {
        return Number.parseFloat((el?.value || "0").replace(/,/g, "")) || 0;
    }

    function moneyText(value) {
        return (Number.parseFloat(value || "0") || 0).toLocaleString("en-IN", {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
        });
    }

    function createFeeHeadRow(name = "", amount = 0) {
        const row = document.createElement("div");
        row.className = "fee-head-row";
        row.dataset.feeHeadRow = "";
        row.draggable = true;
        row.innerHTML = `
            <button class="fee-drag" type="button" title="Drag to reorder"><i class="bi bi-grip-vertical"></i></button>
            <input class="form-control" name="fee_head_name" value="${escapeHtml(name)}" placeholder="Fee Head Name" required>
            <input class="form-control" name="fee_head_amount" value="${Number.parseFloat(amount || 0) || 0}" inputmode="decimal" placeholder="Amount" data-fee-amount required>
            <button class="icon-btn danger" type="button" title="Delete" data-remove-fee-head><i class="bi bi-trash"></i></button>
        `;
        return row;
    }

    function initFeeBuilder(builder) {
        const list = builder.querySelector("[data-fee-head-list]");
        const add = builder.querySelector("[data-add-fee-head]");
        const totalLabel = builder.querySelector("[data-builder-total]");
        const totalInput = builder.querySelector("[data-total-fee]");
        const countLabel = builder.querySelector("[data-builder-count]");
        const error = builder.querySelector("[data-fee-error]");
        const discountField = builder.querySelector("[data-discount]");
        const netField = builder.querySelector("[data-net]");
        let dragged = null;
        if (!list) return;

        function calculate() {
            const rows = Array.from(list.querySelectorAll("[data-fee-head-row]"));
            const names = new Set();
            let duplicate = false;
            let total = 0;
            rows.forEach((row) => {
                const nameInput = row.querySelector("[name='fee_head_name']");
                const amountInput = row.querySelector("[name='fee_head_amount']");
                amountInput.value = amountInput.value.replace(/[^\d.]/g, "");
                const key = (nameInput.value || "").trim().toLowerCase();
                row.classList.remove("has-error");
                if (key) {
                    if (names.has(key)) {
                        duplicate = true;
                        row.classList.add("has-error");
                    }
                    names.add(key);
                }
                total += numberValue(amountInput);
            });
            if (totalLabel) totalLabel.textContent = moneyText(total);
            if (totalInput) totalInput.value = total.toFixed(2);
            if (countLabel) countLabel.textContent = rows.filter((row) => row.querySelector("[name='fee_head_name']")?.value.trim()).length;
            if (netField) netField.value = Math.max(total - numberValue(discountField), 0).toFixed(2);
            if (error) error.textContent = duplicate ? (builder.dataset.duplicateMessage || "Duplicate fee head names are not allowed.") : "";
            return !duplicate;
        }

        add?.addEventListener("click", () => {
            list.appendChild(createFeeHeadRow("Miscellaneous Fee", 0));
            calculate();
        });
        list.addEventListener("input", calculate);
        list.addEventListener("click", (event) => {
            const remove = event.target.closest("[data-remove-fee-head]");
            if (!remove) return;
            remove.closest("[data-fee-head-row]")?.remove();
            if (!list.querySelector("[data-fee-head-row]")) {
                list.appendChild(createFeeHeadRow("Tuition Fee", 0));
            }
            calculate();
        });
        list.addEventListener("dragstart", (event) => {
            dragged = event.target.closest("[data-fee-head-row]");
            dragged?.classList.add("is-dragging");
        });
        list.addEventListener("dragend", () => {
            dragged?.classList.remove("is-dragging");
            dragged = null;
            calculate();
        });
        list.addEventListener("dragover", (event) => {
            event.preventDefault();
            const row = event.target.closest("[data-fee-head-row]");
            if (!row || !dragged || row === dragged) return;
            const bounds = row.getBoundingClientRect();
            list.insertBefore(dragged, event.clientY < bounds.top + bounds.height / 2 ? row : row.nextSibling);
        });
        builder.addEventListener("submit", (event) => {
            if (!calculate()) {
                event.preventDefault();
            }
        });
        discountField?.addEventListener("input", calculate);
        calculate();
    }

    document.querySelectorAll("[data-fee-builder]").forEach(initFeeBuilder);

    const studentFeeForm = document.querySelector("[data-student-fee-form]");
    const studentFeeSelectors = document.querySelectorAll("[data-fee-lookup]");
    const feeStatus = document.querySelector("[data-fee-status]");
    const feeMode = document.querySelector("[data-fee-mode]");
    const existingFeeFields = document.querySelectorAll("[data-existing-fee-field]");
    const manualFeeSection = document.querySelector("[data-manual-fee-section]");
    const structureYear = document.querySelector("[data-structure-year]");
    const structureSelect = document.querySelector("[data-structure-select]");
    const existingFeePreview = document.querySelector("[data-existing-fee-preview]");

    function setStudentFeeMode() {
        const manual = feeMode?.value === "manual";
        existingFeeFields.forEach((field) => field.classList.toggle("d-none", manual));
        manualFeeSection?.classList.toggle("d-none", !manual);
        existingFeePreview?.classList.toggle("d-none", manual);
        if (!manual) loadStudentFee();
    }

    async function loadAvailableStructures() {
        if (!studentFeeForm || !structureSelect || !structureYear) return;
        const selected = structureSelect.dataset.selectedStructure || structureSelect.value || "";
        const params = new URLSearchParams();
        if (structureYear.value) params.set("academic_year", structureYear.value);
        const grade = document.querySelector("[name='grade']")?.value;
        if (grade) params.set("grade", grade);
        const response = await fetch(`/students/fee-structures?${params.toString()}`);
        const rows = await response.json();
        structureSelect.innerHTML = "<option value=''>Auto match by class/type</option>";
        rows.forEach((row) => {
            const option = document.createElement("option");
            option.value = row.id;
            option.textContent = `${row.academic_year} - ${row.grade} - ${row.name} (${moneyText(row.total_amount)})`;
            if (row.id === selected) option.selected = true;
            structureSelect.appendChild(option);
        });
    }

    async function loadStudentFee() {
        const year = document.querySelector("[name='academic_year']")?.value;
        const grade = document.querySelector("[name='grade']")?.value;
        const type = document.querySelector("[name='student_type']")?.value;
        if (feeMode?.value === "manual") return;
        if (!studentFeeForm || !year || !grade || !type) return;
        if (feeStatus) {
            feeStatus.textContent = "Loading matching fee structure...";
            feeStatus.className = "fee-lookup-status text-primary small";
        }
        const lookupYear = structureYear?.value || year;
        const selectedStructure = structureSelect?.value || "";
        const response = await fetch(`/students/fee-lookup?academic_year=${encodeURIComponent(lookupYear)}&grade=${encodeURIComponent(grade)}&student_type=${encodeURIComponent(type)}&fee_structure_id=${encodeURIComponent(selectedStructure)}`);
        const data = await response.json();
        document.querySelector("[data-total-fee]")?.setAttribute("value", Number(data.total_fee || 0).toFixed(2));
        const totalInput = document.querySelector("[data-total-fee]");
        if (totalInput) totalInput.value = Number(data.total_fee || 0).toFixed(2);
        const builderTotal = document.querySelector("[data-builder-total]");
        if (builderTotal) builderTotal.textContent = moneyText(data.total_fee);
        const builderCount = document.querySelector("[data-builder-count]");
        if (builderCount) builderCount.textContent = (data.fee_heads || []).length;
        if (structureSelect && data.fee_structure_id) {
            structureSelect.value = data.fee_structure_id;
            structureSelect.dataset.selectedStructure = data.fee_structure_id;
        }
        const netField = document.querySelector("[data-net]");
        const discountField = document.querySelector("[data-discount]");
        if (netField) netField.value = Math.max(Number(data.total_fee || 0) - numberValue(discountField), 0).toFixed(2);
        Object.entries(data).forEach(([key, value]) => {
            const preview = document.querySelector(`[data-legacy-fee='${key}']`);
            if (preview) preview.textContent = moneyText(value);
        });
        if (feeStatus) {
            if (data.structure_found) {
                feeStatus.textContent = `${data.fee_structure_name || "Fee structure"} loaded.`;
                feeStatus.className = "fee-lookup-status text-success small";
            } else {
                feeStatus.textContent = `No fee structure found for ${grade} / ${type}. Create it in Fee Structure first.`;
                feeStatus.className = "fee-lookup-status text-danger small";
            }
        }
    }
    studentFeeSelectors.forEach((el) => {
        el.addEventListener("change", () => {
            loadAvailableStructures().then(loadStudentFee);
        });
        el.addEventListener("input", () => {
            loadAvailableStructures().then(loadStudentFee);
        });
    });
    feeMode?.addEventListener("change", setStudentFeeMode);
    structureYear?.addEventListener("input", () => loadAvailableStructures().then(loadStudentFee));
    structureSelect?.addEventListener("change", () => {
        structureSelect.dataset.selectedStructure = structureSelect.value;
        loadStudentFee();
    });
    if (studentFeeForm) {
        setStudentFeeMode();
        loadAvailableStructures().then(loadStudentFee);
    }

    const gradeSelect = document.querySelector("[data-student-grade]");
    const studentSelect = document.querySelector("[data-student-select]");
    gradeSelect?.addEventListener("change", async () => {
        studentSelect.innerHTML = "<option value=''>Select Student</option>";
        if (!gradeSelect.value) return;
        const response = await fetch(`/students/api/by-grade/${encodeURIComponent(gradeSelect.value)}`);
        const rows = await response.json();
        rows.forEach((row) => {
            const option = document.createElement("option");
            option.value = row.id;
            option.textContent = row.text;
            studentSelect.appendChild(option);
        });
    });

    const receiptFilterGrade = document.querySelector("[data-receipt-filter-grade]");
    const receiptFilterStudent = document.querySelector("[data-receipt-filter-student]");
    if (receiptFilterGrade && receiptFilterStudent) {
        async function loadReceiptFilterStudents() {
            const selectedStudent = receiptFilterStudent.dataset.selectedStudent || "";
            const params = new URLSearchParams();
            if (receiptFilterGrade.value) {
                params.set("grade", receiptFilterGrade.value);
            }
            receiptFilterStudent.innerHTML = "<option value=''>Loading students...</option>";
            const response = await fetch(`/receipts/api/students?${params.toString()}`);
            const rows = await response.json();
            receiptFilterStudent.innerHTML = "<option value=''>All Students</option>";
            rows.forEach((row) => {
                const option = document.createElement("option");
                option.value = row.id;
                option.textContent = `${row.student_name}${row.admission_no ? ` (${row.admission_no})` : ""}`;
                if (row.id === selectedStudent) {
                    option.selected = true;
                }
                receiptFilterStudent.appendChild(option);
            });
        }
        receiptFilterGrade.addEventListener("change", () => {
            receiptFilterStudent.dataset.selectedStudent = "";
            loadReceiptFilterStudents();
        });
    }

    const receiptForm = document.querySelector("[data-receipt-form]");
    if (receiptForm) {
        const grade = document.querySelector("[data-receipt-grade]");
        const student = document.querySelector("[data-receipt-student]");
        const year = document.querySelector("[data-receipt-year]");
        const studentId = document.querySelector("[data-receipt-student-id]");
        const feeStructureId = document.querySelector("[data-receipt-fee-structure-id]");
        const admission = document.querySelector("[data-receipt-admission]");
        const mobile = document.querySelector("[data-receipt-mobile]");
        const totalFee = document.querySelector("[data-receipt-total-fee]");
        const paidBefore = document.querySelector("[data-receipt-paid-before]");
        const pending = document.querySelector("[data-receipt-pending]");
        const payment = document.querySelector("[data-current-payment]");
        const discount = document.querySelector("[data-current-discount]");
        const previewPending = document.querySelector("[data-preview-pending]");
        const previewPayment = document.querySelector("[data-preview-payment]");
        const previewDiscount = document.querySelector("[data-preview-discount]");
        const previewBalance = document.querySelector("[data-preview-balance]");
        const feeHeads = document.querySelector("[data-fee-heads]");
        const submit = document.querySelector("[data-receipt-submit]");
        let pendingValue = 0;

        const dateInput = receiptForm.querySelector("[name='receipt_date']");
        if (dateInput && !dateInput.value) {
            dateInput.value = new Date().toISOString().slice(0, 10);
        }

        function formatAmount(value) {
            return (Number.parseFloat(value || "0") || 0).toLocaleString("en-IN", {
                minimumFractionDigits: 2,
                maximumFractionDigits: 2,
            });
        }

        function receiptNumber(el) {
            return Number.parseFloat((el?.value || "0").replace(/,/g, "")) || 0;
        }

        function updateReceiptPreview() {
            const current = receiptNumber(payment);
            const currentDiscount = receiptNumber(discount);
            const remaining = Math.max(pendingValue - currentDiscount - current, 0);
            previewPending.textContent = formatAmount(pendingValue);
            previewPayment.textContent = formatAmount(current);
            previewDiscount.textContent = formatAmount(currentDiscount);
            previewBalance.textContent = formatAmount(remaining);
            const invalid = current <= 0 || current > Math.max(pendingValue - currentDiscount, 0);
            submit.disabled = invalid || !studentId.value;
            payment.classList.toggle("is-invalid", current > Math.max(pendingValue - currentDiscount, 0));
        }

        grade?.addEventListener("change", async () => {
            student.innerHTML = "<option value=''>Loading students...</option>";
            studentId.value = "";
            if (!grade.value) {
                student.innerHTML = "<option value=''>Select student</option>";
                return;
            }
            const response = await fetch(`/receipts/api/students?academic_year=${encodeURIComponent(year.value)}&grade=${encodeURIComponent(grade.value)}`);
            const rows = await response.json();
            student.innerHTML = "<option value=''>Select student</option>";
            rows.forEach((row) => {
                const option = document.createElement("option");
                option.value = row.id;
                option.textContent = `${row.student_name} (${row.admission_no || "No Admission No"})`;
                student.appendChild(option);
            });
        });

        student?.addEventListener("change", async () => {
            studentId.value = student.value;
            if (!student.value) return;
            const response = await fetch(`/receipts/api/student/${encodeURIComponent(student.value)}`);
            const data = await response.json();
            feeStructureId.value = data.fee_structure_id || "";
            admission.value = data.admission_no || "";
            mobile.value = data.mobile || "";
            totalFee.value = data.total_fee_display || "0.00";
            paidBefore.value = data.previously_paid_display || "0.00";
            pending.value = data.pending_due_display || "0.00";
            pendingValue = Number(data.pending_due || 0);
            payment.value = "";
            discount.value = "0";
            feeHeads.innerHTML = "";
            (data.fee_heads || []).forEach((head) => {
                const item = document.createElement("div");
                item.innerHTML = `<span>${head.label}</span><strong>${formatAmount(head.amount)}</strong>`;
                feeHeads.appendChild(item);
            });
            if (!data.structure_found) {
                const warning = document.createElement("div");
                warning.className = "text-danger";
                warning.innerHTML = "<span>Fee Structure</span><strong>Not found for this class/type</strong>";
                feeHeads.prepend(warning);
            }
            updateReceiptPreview();
        });

        payment?.addEventListener("input", updateReceiptPreview);
        discount?.addEventListener("input", updateReceiptPreview);
        updateReceiptPreview();
    }

    if (window.Chart && window.dashboardPaymentModes) {
        const colors = ["#16a34a", "#2563eb", "#7c3aed", "#f97316", "#64748b", "#0f172a"];
        const modeCanvas = document.getElementById("paymentModeChart");
        if (modeCanvas) {
            new Chart(modeCanvas, {
                type: "doughnut",
                data: {
                    labels: window.dashboardPaymentModes.labels,
                    datasets: [{
                        data: window.dashboardPaymentModes.data,
                        backgroundColor: colors,
                        borderWidth: 0,
                    }],
                },
                options: {
                    responsive: true,
                    cutout: "68%",
                    plugins: {
                        legend: { position: "bottom", labels: { boxWidth: 10, usePointStyle: true } },
                    },
                },
            });
        }
        const trendCanvas = document.getElementById("paymentTrendChart");
        if (trendCanvas && window.dashboardPaymentTrend) {
            new Chart(trendCanvas, {
                type: "line",
                data: {
                    labels: window.dashboardPaymentTrend.labels,
                    datasets: window.dashboardPaymentTrend.datasets.map((dataset, index) => ({
                        ...dataset,
                        borderColor: colors[index % colors.length],
                        backgroundColor: `${colors[index % colors.length]}22`,
                        tension: 0.38,
                        fill: false,
                        pointRadius: 3,
                    })),
                },
                options: {
                    responsive: true,
                    plugins: { legend: { position: "bottom" } },
                    scales: {
                        y: { beginAtZero: true, grid: { color: "#e5edf7" } },
                        x: { grid: { display: false } },
                    },
                },
            });
        }
    }

    function formatStudentAmount(value) {
        return (Number.parseFloat(value || "0") || 0).toLocaleString("en-IN", {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
        });
    }

    function escapeHtml(value) {
        return String(value ?? "").replace(/[&<>"']/g, (char) => ({
            "&": "&amp;",
            "<": "&lt;",
            ">": "&gt;",
            '"': "&quot;",
            "'": "&#039;",
        }[char]));
    }

    function profileField(label, value) {
        return `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value || "-")}</strong></div>`;
    }

    const profileDrawerEl = document.getElementById("studentProfileDrawer");
    const profileDrawer = profileDrawerEl && window.bootstrap ? new bootstrap.Offcanvas(profileDrawerEl) : null;
    const profileLoading = document.querySelector("[data-profile-loading]");
    const profileContent = document.querySelector("[data-profile-content]");

    async function openStudentProfile(studentId) {
        if (!profileDrawer) return;
        profileLoading?.classList.remove("d-none");
        profileContent?.classList.add("d-none");
        profileDrawer.show();
        const response = await fetch(`/students/${encodeURIComponent(studentId)}/api`);
        const data = await response.json();
        if (!response.ok) {
            if (profileLoading) profileLoading.textContent = data.error || "Unable to load profile.";
            return;
        }
        const student = data.student || {};
        const fee = data.fee || {};
        const initials = (student.student_name || "ST").split(/\s+/).slice(0, 2).map((part) => part[0]).join("").toUpperCase();
        document.querySelector("[data-profile-initials]").textContent = initials || "ST";
        document.querySelector("[data-profile-name]").textContent = student.student_name || "Student";
        document.querySelector("[data-profile-meta]").textContent = `${student.admission_no || "-"} - ${student.grade || "-"} - ${student.academic_year || "-"}`;
        document.querySelector("[data-profile-status]").outerHTML = `<span data-profile-status class="student-status-pill status-${String(student.status || "Active").toLowerCase()}">${escapeHtml(student.status || "Active")}</span>`;
        document.querySelector("[data-profile-overview]").innerHTML = [
            profileField("Admission No", student.admission_no),
            profileField("Roll No", student.roll_no),
            profileField("Grade", student.grade),
            profileField("Student Type", student.student_type),
            profileField("Mobile", student.mobile),
            profileField("Alternate Number", student.alternate_number),
            profileField("Father Name", student.father_name),
            profileField("Mother Name", student.mother_name),
            profileField("Address", student.address),
            profileField("TC Issued", student.tc_issued ? "Yes" : "No"),
        ].join("");
        document.querySelector("[data-profile-fees]").innerHTML = [
            profileField("Total Fee", formatStudentAmount(fee.total_fee)),
            profileField("Discount", formatStudentAmount(fee.discount)),
            profileField("Net Receivable", formatStudentAmount(fee.net_receivable)),
            profileField("Total Paid", formatStudentAmount(fee.total_paid)),
            profileField("Balance Due", formatStudentAmount(fee.balance_due)),
        ].join("");
        document.querySelector("[data-profile-fee-heads]").innerHTML = (fee.heads || []).map((head) => (
            `<div><span>${escapeHtml(head.label)}</span><strong>${formatStudentAmount(head.amount)}</strong></div>`
        )).join("");
        document.querySelector("[data-profile-receipts]").innerHTML = (data.payments || []).length
            ? data.payments.map((payment) => `<div><span>${escapeHtml(payment.receipt_date || "-")} - ${escapeHtml(payment.payment_mode || "-")}</span><strong>${escapeHtml(payment.receipt_no || "-")} - ${formatStudentAmount(payment.amount_paid)}</strong></div>`).join("")
            : "<div><span>Receipts</span><strong>No receipts recorded</strong></div>";
        document.querySelector("[data-profile-history]").innerHTML = (data.history || []).map((item) => (
            `<div><span>${escapeHtml(item.academic_year || "-")} - ${escapeHtml(item.status || "Active")}</span><strong>${escapeHtml(item.grade || "-")} ${item.previous_grade ? `from ${escapeHtml(item.previous_grade)}` : ""}</strong></div>`
        )).join("");
        profileLoading?.classList.add("d-none");
        profileContent?.classList.remove("d-none");
    }

    document.querySelectorAll("[data-student-drawer]").forEach((button) => {
        button.addEventListener("click", () => openStudentProfile(button.dataset.studentDrawer));
    });

    const promoteModalEl = document.getElementById("promoteStudentModal");
    const promoteModal = promoteModalEl && window.bootstrap ? new bootstrap.Modal(promoteModalEl) : null;
    const promoteForm = document.querySelector("[data-promote-form]");
    const keepExistingFee = document.querySelector("[data-keep-existing-fee]");
    const promotionNewFeeFields = document.querySelectorAll("[data-promotion-new-fee]");
    function syncPromotionFeeFields() {
        promotionNewFeeFields.forEach((field) => field.classList.toggle("d-none", keepExistingFee?.checked));
    }
    keepExistingFee?.addEventListener("change", syncPromotionFeeFields);
    document.querySelectorAll("[data-promote-student]").forEach((button) => {
        button.addEventListener("click", () => {
            if (!promoteForm || !promoteModal) return;
            promoteForm.action = `/students/${encodeURIComponent(button.dataset.promoteStudent)}/promote`;
            document.querySelector("[data-promote-name]").textContent = button.dataset.name || "";
            document.querySelector("[data-promote-year]").value = button.dataset.year || "";
            document.querySelector("[data-promote-grade]").value = button.dataset.grade || "";
            if (keepExistingFee) keepExistingFee.checked = true;
            syncPromotionFeeFields();
            promoteModal.show();
        });
    });
    syncPromotionFeeFields();

    const leftModalEl = document.getElementById("markLeftModal");
    const leftModal = leftModalEl && window.bootstrap ? new bootstrap.Modal(leftModalEl) : null;
    const leftForm = document.querySelector("[data-left-form]");
    document.querySelectorAll("[data-left-student]").forEach((button) => {
        button.addEventListener("click", () => {
            if (!leftForm || !leftModal) return;
            leftForm.action = `/students/${encodeURIComponent(button.dataset.leftStudent)}/mark-left`;
            document.querySelector("[data-left-name]").textContent = button.dataset.name || "";
            const dateInput = leftForm.querySelector("[name='left_date']");
            if (dateInput && !dateInput.value) dateInput.value = new Date().toISOString().slice(0, 10);
            leftModal.show();
        });
    });

    const bulkForm = document.querySelector("[data-bulk-promote-form]");
    const bulkLoad = document.querySelector("[data-bulk-load]");
    const bulkStudents = document.querySelector("[data-bulk-students]");
    bulkLoad?.addEventListener("click", async () => {
        if (!bulkForm || !bulkStudents) return;
        const year = bulkForm.querySelector("[data-bulk-year]")?.value || "";
        const grade = bulkForm.querySelector("[data-bulk-current-grade]")?.value || "";
        if (!year || !grade) {
            bulkStudents.innerHTML = "<span class='text-danger'>Select current academic year and class first.</span>";
            return;
        }
        bulkStudents.innerHTML = "<span class='text-muted'>Loading eligible students...</span>";
        const response = await fetch(`/students/api/eligible?academic_year=${encodeURIComponent(year)}&grade=${encodeURIComponent(grade)}`);
        const rows = await response.json();
        bulkStudents.innerHTML = rows.length
            ? rows.map((row) => `<label><input class="form-check-input" type="checkbox" name="student_ids" value="${escapeHtml(row.id)}" checked><span>${escapeHtml(row.text)}</span></label>`).join("")
            : "<span class='text-muted'>No eligible active students found for this class.</span>";
    });

    bulkForm?.addEventListener("submit", (event) => {
        const selected = bulkForm.querySelectorAll("input[name='student_ids']:checked").length;
        if (!selected || !confirm(`Promote ${selected} selected student(s)?`)) {
            event.preventDefault();
        }
    });
});
