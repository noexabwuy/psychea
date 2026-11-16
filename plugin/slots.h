/* Which table row belongs to a key (a driver structure or endpoint array address). Pure code, tested in test_slots.c.
 * A module loaded again lands on the same address or on another one: it finds its old row, or takes a freed one. */
#ifndef PSYCHEA_SLOTS_H
#define PSYCHEA_SLOTS_H

/* keys[0..*n) are rows; 0 marks a free row. Returns the row of key, reusing a free row or appending when it is new;
 * -1 when the table is full or key is 0. */
static int key_slot(unsigned int *keys, int *n, int max, unsigned int key)
{
	int i, f = -1;
	if (!key) return -1;
	for (i = 0; i < *n; i++) {
		if (keys[i] == key) return i;
		if (!keys[i] && f < 0) f = i;
	}
	if (f >= 0) { keys[f] = key; return f; }
	if (*n >= max) return -1;
	keys[*n] = key;
	return (*n)++;
}

/* frees the row of key (the driver was unregistered); returns the row, or -1 when there is none */
static int key_free(unsigned int *keys, int n, unsigned int key)
{
	int i;
	if (!key) return -1;
	for (i = 0; i < n; i++)
		if (keys[i] == key) { keys[i] = 0; return i; }
	return -1;
}

#endif
