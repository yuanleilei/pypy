from lib_pypy import transaction

N = 1000
VERBOSE = False


def test_simple_random_order():
    for x in range(N):
        lst = []
        for i in range(10):
            transaction.add(lst.append, i)
        transaction.run()
        if VERBOSE:
            print lst
        assert sorted(lst) == range(10), lst

def test_simple_fixed_order():
    for x in range(N):
        lst = []
        def do_stuff(i):
            lst.append(i)
            i += 1
            if i < 10:
                transaction.add(do_stuff, i)
        transaction.add(do_stuff, 0)
        transaction.run()
        if VERBOSE:
            print lst
        assert lst == range(10), lst

def test_simple_random_and_fixed_order():
    for x in range(N):
        lsts = ([], [], [], [], [])
        def do_stuff(i, j):
            lsts[i].append(j)
            j += 1
            if j < 10:
                transaction.add(do_stuff, i, j)
        for i in range(5):
            transaction.add(do_stuff, i, 0)
        transaction.run()
        if VERBOSE:
            print lsts
        assert lsts == (range(10),) * 5, lsts

def test_raise():
    class FooError(Exception):
        pass
    for x in range(N):
        lsts = ([], [], [], [], [], [], [], [], [], [])
        def do_stuff(i, j):
            lsts[i].append(j)
            j += 1
            if j < 5:
                transaction.add(do_stuff, i, j)
            else:
                lsts[i].append('foo')
                raise FooError
        for i in range(10):
            transaction.add(do_stuff, i, 0)
        try:
            transaction.run()
        except FooError:
            pass
        else:
            raise AssertionError("should have raised FooError")
        if VERBOSE:
            print lsts
        num_foos = 0
        for lst in lsts:
            if len(lst) < 5:
                assert lst == range(len(lst)), lst
            else:
                assert lst == range(5) + ['foo'], lst
                num_foos += 1
        assert num_foos == 1, lsts


def test_number_of_transactions_reported():
    transaction.add(lambda: None)
    transaction.run()
    assert transaction.number_of_transactions_in_last_run() == 1

    def add_transactions(l):
        if l:
            for x in range(l[0]):
                transaction.add(add_transactions, l[1:])

    transaction.add(add_transactions, [10, 10, 10])
    transaction.run()
    assert transaction.number_of_transactions_in_last_run() == 1111


def run_tests():
    for name in sorted(globals().keys()):
        if name.startswith('test_'):
            value = globals().get(name)
            if type(value) is type(run_tests):
                print name
                value()
    print 'all tests passed.'

if __name__ == '__main__':
    run_tests()
