
static __thread void *rpython_tls_object;

void stm_set_tls(void *newtls)
{
  rpython_tls_object = newtls;
}

void *stm_get_tls(void)
{
  return rpython_tls_object;
}

void stm_del_tls(void)
{
  rpython_tls_object = NULL;
}

gcptr stm_tldict_lookup(gcptr key)
{
  struct tx_descriptor *d = thread_descriptor;
  wlog_t* found;
  G2L_FIND(d->global_to_local, key, found, goto not_found);
  return found->val;

 not_found:
  return NULL;
}

void stm_tldict_add(gcptr key, gcptr value)
{
  struct tx_descriptor *d = thread_descriptor;
  assert(d != NULL);
  g2l_insert(&d->global_to_local, key, value);
}

void stm_tldict_enum(void)
{
  struct tx_descriptor *d = thread_descriptor;
  wlog_t *item;
  void *tls = stm_get_tls();

  G2L_LOOP_FORWARD(d->global_to_local, item)
    {
      gcptr R = item->addr;
      gcptr L = item->val;
      assert(L->h_revision == (revision_t)R);
      if ((L->h_tid & GCFLAG_NOT_WRITTEN) == 0)
        pypy_g__stm_enum_callback(tls, L);
    } G2L_LOOP_END;
}

long stm_in_transaction(void)
{
  struct tx_descriptor *d = thread_descriptor;
  return d->active;
}

long stm_is_inevitable(void)
{
  struct tx_descriptor *d = thread_descriptor;
  return is_inevitable(d);
}

static unsigned long stm_regular_length_limit = ULONG_MAX;
static volatile int break_please = 0;

static void reached_safe_point(void);

void stm_add_atomic(long delta)
{
  struct tx_descriptor *d = thread_descriptor;
  d->atomic += delta;
  update_reads_size_limit(d);
}

long stm_get_atomic(void)
{
  struct tx_descriptor *d = thread_descriptor;
  return d->atomic;
}

long stm_should_break_transaction(void)
{
  struct tx_descriptor *d = thread_descriptor;

  /* a single comparison to handle all cases:

     - if d->atomic, then we should return False.  This is done by
       forcing reads_size_limit to ULONG_MAX as soon as atomic > 0,
       and no possible value of 'count_reads' is greater than ULONG_MAX.

     - otherwise, if is_inevitable(), then we should return True.
       This is done by forcing both reads_size_limit and
       reads_size_limit_nonatomic to 0 in that case.

     - finally, the default case: return True if
       d->count_reads is
       greater than reads_size_limit == reads_size_limit_nonatomic.
  */
#if 0   /* ifdef RPY_STM_ASSERT */
  /* reads_size_limit is ULONG_MAX if d->atomic, or else it is equal to
     reads_size_limit_nonatomic. */
  assert(d->reads_size_limit == (d->atomic ? ULONG_MAX :
                                     d->reads_size_limit_nonatomic));
  /* if is_inevitable(), reads_size_limit_nonatomic should be 0
     (and thus reads_size_limit too, if !d->atomic.) */
  if (is_inevitable(d))
    assert(d->reads_size_limit_nonatomic == 0);
#endif

  if (break_please)
    reached_safe_point();

  return d->count_reads > d->reads_size_limit;
}

void stm_set_transaction_length(long length_max)
{
  struct tx_descriptor *d = thread_descriptor;
  BecomeInevitable("set_transaction_length");
  if (length_max <= 0)
    length_max = 1;
  stm_regular_length_limit = length_max;
}

#define END_MARKER   ((void*)-8)   /* keep in sync with stmframework.py */

void stm_perform_transaction(long(*callback)(void*, long), void *arg,
                             void *save_and_restore)
{
  jmp_buf _jmpbuf;
  long volatile v_counter = 0;
  void **volatile v_saved_value;
  long volatile v_atomic = thread_descriptor->atomic;
  assert((!thread_descriptor->active) == (!v_atomic));
#ifndef USING_NO_GC_AT_ALL
  v_saved_value = *(void***)save_and_restore;
#endif
  PYPY_DEBUG_START("stm-perform-transaction");
  /***/
  setjmp(_jmpbuf);
  /* After setjmp(), the local variables v_* are preserved because they
   * are volatile.  The other variables are only declared here. */
  struct tx_descriptor *d = thread_descriptor;
  long counter, result;
  void **restore_value;
  counter = v_counter;
  d->atomic = v_atomic;
#ifndef USING_NO_GC_AT_ALL
  restore_value = v_saved_value;
  if (!d->atomic)
    {
      /* In non-atomic mode, we are now between two transactions.
         It means that in the next transaction's collections we know
         that we won't need to access the shadows stack beyond its
         current position.  So we add an end marker. */
      *restore_value++ = END_MARKER;
    }
  *(void***)save_and_restore = restore_value;
#endif

  do
    {
      v_counter = counter + 1;
      /* If counter==0, initialize 'reads_size_limit_nonatomic' from the
         configured length limit.  If counter>0, we did an abort, which
         has configured 'reads_size_limit_nonatomic' to a smaller value.
         When such a shortened transaction succeeds, the next one will
         see its length limit doubled, up to the maximum. */
      if (counter == 0) {
          unsigned long limit = d->reads_size_limit_nonatomic;
          if (limit != 0 && limit < (stm_regular_length_limit >> 1))
              limit = (limit << 1) | 1;
          else
              limit = stm_regular_length_limit;
          d->reads_size_limit_nonatomic = limit;
      }
      if (!d->atomic)
        BeginTransaction(&_jmpbuf);

      if (break_please)
        reached_safe_point();

      /* invoke the callback in the new transaction */
      result = callback(arg, counter);

      v_atomic = d->atomic;
      if (!d->atomic)
        CommitTransaction();
      counter = 0;
    }
  while (result == 1);  /* also stops if we got an RPython exception */

  if (d->atomic && d->setjmp_buf == &_jmpbuf)
    BecomeInevitable("perform_transaction left with atomic");

#ifndef USING_NO_GC_AT_ALL
  *(void***)save_and_restore = v_saved_value;
#endif
  PYPY_DEBUG_STOP("stm-perform-transaction");
}

static struct tx_descriptor *in_single_thread = NULL;  /* for debugging */

void stm_start_single_thread(void)
{
  /* Called by the GC, just after a minor collection, when we need to do
     a major collection.  When it returns, it acquired the "write lock"
     which prevents any other thread from running a transaction. */
  int err;
  break_please = 1;
  err = pthread_rwlock_unlock(&rwlock_in_transaction);
  assert(err == 0);
  err = pthread_rwlock_wrlock(&rwlock_in_transaction);
  assert(err == 0);
  break_please = 0;

  assert(in_single_thread == NULL);
  in_single_thread = thread_descriptor;
  assert(in_single_thread != NULL);
}

void stm_stop_single_thread(void)
{
  int err;

  assert(in_single_thread == thread_descriptor);
  in_single_thread = NULL;

  err = pthread_rwlock_unlock(&rwlock_in_transaction);
  assert(err == 0);
  err = pthread_rwlock_rdlock(&rwlock_in_transaction);
  assert(err == 0);
}

static void reached_safe_point(void)
{
  int err;
  struct tx_descriptor *d = thread_descriptor;
  assert(in_single_thread != d);
  if (d->active)
    {
      err = pthread_rwlock_unlock(&rwlock_in_transaction);
      assert(err == 0);
      err = pthread_rwlock_rdlock(&rwlock_in_transaction);
      assert(err == 0);
    }
}

void stm_abort_and_retry(void)
{
  AbortTransaction(4);    /* manual abort */
}

void stm_abort_info_push(void *obj, void *fieldoffsets)
{
    struct tx_descriptor *d = thread_descriptor;
    gcptr P = (gcptr)obj;
    if ((P->h_tid & GCFLAG_GLOBAL) &&
        (P->h_tid & GCFLAG_POSSIBLY_OUTDATED)) {
        P = LatestGlobalRevision(d, P, NULL, 0);
    }
    gcptrlist_insert2(&d->abortinfo, P, (gcptr)fieldoffsets);
}

void stm_abort_info_pop(long count)
{
    struct tx_descriptor *d = thread_descriptor;
    long newsize = d->abortinfo.size - 2 * count;
    gcptrlist_reduce_size(&d->abortinfo, newsize < 0 ? 0 : newsize);
}

size_t _stm_decode_abort_info(struct tx_descriptor *d, long long elapsed_time,
                              int abort_reason, char *output)
{
    /* re-encodes the abort info as a single string.
       For convenience (no escaping needed, no limit on integer
       sizes, etc.) we follow the bittorrent format. */
    size_t totalsize = 0;
    long i;
    char buffer[32];
    size_t res_size;
#define WRITE(c)   { totalsize++; if (output) *output++=(c); }
#define WRITE_BUF(p, sz)  { totalsize += (sz);                          \
                            if (output) {                               \
                                 memcpy(output, (p), (sz)); output += (sz); \
                             }                                          \
                           }
    WRITE('l');
    WRITE('l');
    res_size = sprintf(buffer, "i%llde", (long long)elapsed_time);
    WRITE_BUF(buffer, res_size);
    res_size = sprintf(buffer, "i%de", (int)abort_reason);
    WRITE_BUF(buffer, res_size);
    res_size = sprintf(buffer, "i%lde", (long)(d->my_lock - LOCKED));
    WRITE_BUF(buffer, res_size);
    res_size = sprintf(buffer, "i%lde", (long)d->atomic);
    WRITE_BUF(buffer, res_size);
    res_size = sprintf(buffer, "i%de", (int)d->active);
    WRITE_BUF(buffer, res_size);
    res_size = sprintf(buffer, "i%lue", (unsigned long)d->count_reads);
    WRITE_BUF(buffer, res_size);
    res_size = sprintf(buffer, "i%lue",
                       (unsigned long)d->reads_size_limit_nonatomic);
    WRITE_BUF(buffer, res_size);
    WRITE('e');
    for (i=0; i<d->abortinfo.size; i+=2) {
        char *object = (char *)stm_RepeatReadBarrier(d->abortinfo.items[i+0]);
        long *fieldoffsets = (long*)d->abortinfo.items[i+1];
        long kind, offset;
        size_t rps_size;
        RPyString *rps;

        while (1) {
            kind = *fieldoffsets++;
            if (kind <= 0) {
                if (kind == -2) {
                    WRITE('l');    /* '[', start of sublist */
                    continue;
                }
                if (kind == -1) {
                    WRITE('e');    /* ']', end of sublist */
                    continue;
                }
                break;   /* 0, terminator */
            }
            offset = *fieldoffsets++;
            switch(kind) {
            case 1:    /* signed */
                res_size = sprintf(buffer, "i%lde",
                                   *(long*)(object + offset));
                WRITE_BUF(buffer, res_size);
                break;
            case 2:    /* unsigned */
                res_size = sprintf(buffer, "i%lue",
                                   *(unsigned long*)(object + offset));
                WRITE_BUF(buffer, res_size);
                break;
            case 3:    /* pointer to STR */
                rps = *(RPyString **)(object + offset);
                if (rps) {
                    rps_size = RPyString_Size(rps);
                    res_size = sprintf(buffer, "%zu:", rps_size);
                    WRITE_BUF(buffer, res_size);
                    WRITE_BUF(_RPyString_AsString(rps), rps_size);
                }
                else {
                    WRITE_BUF("0:", 2);
                }
                break;
            default:
                fprintf(stderr, "Fatal RPython error: corrupted abort log\n");
                abort();
            }
        }
    }
    WRITE('e');
    WRITE('\0');   /* final null character */
#undef WRITE
    return totalsize;
}

char *stm_inspect_abort_info(void)
{
    struct tx_descriptor *d = thread_descriptor;
    if (d->longest_abort_info_time <= 0)
        return NULL;
    d->longest_abort_info_time = 0;
    return d->longest_abort_info;
}

long stm_extraref_llcount(void)
{
    struct tx_descriptor *d = thread_descriptor;
    return d->abortinfo.size / 2;
}

gcptr *stm_extraref_lladdr(long index)
{
    struct tx_descriptor *d = thread_descriptor;
    return &d->abortinfo.items[index * 2];
}

#ifdef USING_NO_GC_AT_ALL
static __thread gcptr stm_nogc_chained_list;
void stm_nogc_start_transaction(void)
{
    stm_nogc_chained_list = NULL;
}
gcptr stm_nogc_allocate(size_t size)
{
    gcptr W = calloc(1, size);
    if (!W) {
        fprintf(stderr, "out of memory!\n");
        abort();
    }
    W->h_size = size;
    W->h_revision = (revision_t)stm_nogc_chained_list;
    stm_nogc_chained_list = W;
    return W;
}
void stm_nogc_stop_transaction(void)
{
    gcptr W = stm_nogc_chained_list;
    stm_nogc_chained_list = NULL;
    while (W) {
        gcptr W_next = (gcptr)W->h_revision;
        assert((W->h_tid & (GCFLAG_GLOBAL |
                            GCFLAG_NOT_WRITTEN |
                            GCFLAG_LOCAL_COPY)) == 0);
        W->h_tid |= GCFLAG_GLOBAL | GCFLAG_NOT_WRITTEN;
        W->h_revision = 1;
        W = W_next;
    }
}
void *pypy_g__stm_duplicate(void *src)
{
    size_t size = ((gcptr)src)->h_size;
    void *result = malloc(size);
    if (!result) {
        fprintf(stderr, "out of memory!\n");
        abort();
    }
    memcpy(result, src, size);
    ((gcptr)result)->h_tid &= ~(GCFLAG_GLOBAL | GCFLAG_POSSIBLY_OUTDATED);
    ((gcptr)result)->h_tid |= GCFLAG_LOCAL_COPY;
    return result;
}
void pypy_g__stm_enum_callback(void *tlsaddr, void *L)
{
    abort();
}
#endif
