@app.route("/buyer/active/orders")
def buyer_active_orders():
    if session.get("role") != "buyer":
        return redirect("/")
    orders = get_buyer_orders("active")
    return render_template("buyer_active_order.html", page_title="Active Orders", orders=orders)


@app.route("/buyer/partial")
def buyer_partial():
    if session.get("role") != "buyer":
        return redirect("/")
    orders = get_buyer_orders("partial")
    return render_template("buyer_active.html", page_title="Partially Closed Orders", orders=orders)


@app.route("/buyer/loaded")
def buyer_loaded():
    if session.get("role") != "buyer":
        return redirect("/")
    orders = get_buyer_orders("loaded")
    return render_template("buyer_active.html", page_title="Loaded Orders", orders=orders)