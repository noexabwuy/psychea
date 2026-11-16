#include <stdio.h>
#include "slots.h"

static int fails;
#define CHECK(c) do { if (!(c)) { printf("FAIL line %d: %s\n", __LINE__, #c); fails++; } } while (0)

int main(void)
{
	unsigned int keys[4] = {0};
	int n = 0;

	CHECK(key_slot(keys, &n, 4, 0x8829298Cu) == 0);
	CHECK(key_slot(keys, &n, 4, 0x88292790u) == 1);
	CHECK(key_slot(keys, &n, 4, 0x880789C4u) == 2);
	CHECK(n == 3);

	CHECK(key_slot(keys, &n, 4, 0x8829298Cu) == 0);     /* the module is loaded again at the same address */
	CHECK(key_slot(keys, &n, 4, 0x88292790u) == 1);
	CHECK(key_slot(keys, &n, 4, 0x880789C4u) == 2);
	CHECK(n == 3);

	CHECK(key_slot(keys, &n, 4, 0x88300000u) == 3);     /* a new address takes the next free slot */
	CHECK(n == 4);
	CHECK(key_slot(keys, &n, 4, 0x88300000u) == 3);
	CHECK(key_slot(keys, &n, 4, 0x88400000u) == -1);    /* table full: no slot, nothing overwritten */
	CHECK(n == 4 && keys[0] == 0x8829298Cu && keys[3] == 0x88300000u);

	n = 0;
	CHECK(key_slot(keys, &n, 0, 1) == -1);              /* no room at all */
	CHECK(n == 0);

	/* the camera drivers are unregistered and registered again at other addresses */
	{
		unsigned int k[4] = {0};
		int cnt = 0;
		CHECK(key_slot(k, &cnt, 4, 0x880789C4u) == 0);          /* acc */
		CHECK(key_slot(k, &cnt, 4, 0x88292B8Cu) == 1);          /* mic */
		CHECK(key_slot(k, &cnt, 4, 0x88292990u) == 2);          /* cam */
		CHECK(key_free(k, cnt, 0x88292990u) == 2);
		CHECK(key_free(k, cnt, 0x88292B8Cu) == 1);
		CHECK(key_free(k, cnt, 0x880789C4u) == 0);
		CHECK(key_free(k, cnt, 0x880789C4u) == -1);             /* already free */
		CHECK(key_free(k, cnt, 0x12345678u) == -1);             /* never registered */
		CHECK(key_slot(k, &cnt, 4, 0x880789C4u) == 0);
		CHECK(key_slot(k, &cnt, 4, 0x882A548Cu) == 1);          /* a new address takes a freed row */
		CHECK(key_slot(k, &cnt, 4, 0x882A5290u) == 2);
		CHECK(cnt == 3);
		CHECK(key_slot(k, &cnt, 4, 0) == -1);                   /* 0 is the free mark, not a key */
		CHECK(key_free(k, cnt, 0x882A5290u) == 2);
		CHECK(key_free(k, cnt, 0) == -1);                       /* a free row is not found by key 0 */
	}

	if (!fails) printf("slots ok\n");
	return fails != 0;
}
