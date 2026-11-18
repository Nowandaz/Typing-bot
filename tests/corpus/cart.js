'use strict';

const fs = require('fs');
const TAX = 0.2;

/* Plain block comment. */
const defaults = {
    currency: 'EUR',
    rounding: 2,
    labels: ['net', 'tax', 'gross'],
};

/**
 * Format a number of cents.
 *
 * @param {number} cents
 * @returns {string}
 */
function format(cents, { currency = 'EUR' } = {}) {
    const amount = (cents / 100).toFixed(defaults.rounding);
    return `${amount} ${currency}`;
}

class Cart {
    #items = [];

    static from(json) {
        const cart = new Cart();
        for (const [name, price] of Object.entries(JSON.parse(json))) {
            cart.add(name, price);
        }
        return cart;
    }

    add(name, price, quantity = 1) {
        if (price < 0) throw new Error("price can't be negative");
        this.#items.push({ name, price, quantity });
        return this;
    }

    get total() {
        return this.#items.reduce((sum, item) => sum + item.price * item.quantity, 0);
    }

    lines() {
        return this.#items
            .filter((item) => item.quantity > 0)
            .map((item) => `${item.name} x${item.quantity}`);
    }
}

async function save(cart, path) {
    const body = JSON.stringify({
        total: cart.total,
        tax: Math.round(cart.total * TAX),
        lines: cart.lines(),
    }, null, 2);
    try {
        await fs.promises.writeFile(path, body);
    } catch (err) {
        console.error(`could not write ${path}: ${err.message}`);
        throw err;
    } finally {
        console.log('done');
    }
}

function discount(total) {
    if (total > 10000)
        return 0.1;
    else if (total > 5000) {
        return 0.05;
    } else {
        return 0;
    }
}

const isEmpty = (cart) => cart.lines().length === 0;
const names = ['a', 'b'].map(n => n.toUpperCase());
let i = 0;
while (i < 3) {
    i++;
}
do {
    i -= 1;
} while (i > 0);

module.exports = { Cart, format, save, discount, isEmpty, names };
