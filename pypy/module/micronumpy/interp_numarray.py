from pypy.interpreter.baseobjspace import Wrappable
from pypy.interpreter.error import OperationError
from pypy.interpreter.gateway import interp2app, unwrap_spec
from pypy.interpreter.typedef import TypeDef, GetSetProperty
from pypy.module.micronumpy import interp_ufuncs, interp_dtype, signature
from pypy.rlib import jit
from pypy.rpython.lltypesystem import lltype
from pypy.tool.sourcetools import func_with_new_name
from traceback import print_exc


numpy_driver = jit.JitDriver(greens = ['signature'],
                             reds = ['result_size', 'i', 'self', 'result'])
all_driver = jit.JitDriver(greens=['signature'], reds=['i', 'size', 'self', 'dtype'])
any_driver = jit.JitDriver(greens=['signature'], reds=['i', 'size', 'self', 'dtype'])
slice_driver = jit.JitDriver(greens=['signature'], reds=['i', 'j', 'step', 'stop', 'source', 'dest'])
nslice_driver = jit.JitDriver(greens=['signature'], reds=['i', 'self', 'count', 'source', 'dest'])

def prod(item):
    res=1
    for i in item:
        res *= i
    return res

class BaseArray(Wrappable):
    _attrs_ = ["invalidates", "signature"]

    def __init__(self):
        self.invalidates = []

    def invalidated(self):
        if self.invalidates:
            self._invalidated()

    def _invalidated(self):
        for arr in self.invalidates:
            arr.force_if_needed()
        del self.invalidates[:]

    def add_invalidates(self, other):
        self.invalidates.append(other)

    def find_dtype(space,w_iterable_or_scalar, w_dtype):
        if w_dtype is space.fromcache(interp_dtype.W_Float64Dtype):
            return w_dtype
        if space.issequence_w(w_iterable_or_scalar):
            w_iterator = space.iter(w_iterable_or_scalar)
            while True:
                try:
                    w_item = space.next(w_iterator)
                except OperationError, e:
                    if not e.match(space, space.w_StopIteration):
                        raise
                    return w_dtype
                w_dtype = BaseArray.find_dtype.im_func(space,w_item, w_dtype)
        else:
            w_dtype = interp_ufuncs.find_dtype_for_scalar(space,w_iterable_or_scalar, w_dtype)
        return w_dtype
             
    def descr__new__(space, w_subtype, w_size_or_iterable, w_dtype=None):
        l = space.listview(w_size_or_iterable)
        if space.is_w(w_dtype, space.w_None):
            w_dtype = None
            for w_item in l:
                w_dtype = interp_ufuncs.find_dtype_for_scalar(space, w_item, w_dtype)
                if w_dtype is space.fromcache(interp_dtype.W_Float64Dtype):
                    break
            if w_dtype is None:
                w_dtype = space.w_None

        dtype = space.interp_w(interp_dtype.W_Dtype,
            space.call_function(space.gettypefor(interp_dtype.W_Dtype), w_dtype)
        )
        w_elem = space.getitem(w_size_or_iterable, space.wrap(0))
        length = len(l)
        if isinstance(w_size_or_iterable,BaseArray) and len(w_size_or_iterable.find_shape())>1:
            shape = w_size_or_iterable.find_shape()
            arr = NDimArray(shape,dtype=dtype)
            arr.setslice(space,((0,length,1,length),) ,w_size_or_iterable)
        elif isinstance(w_size_or_iterable,BaseArray):
            arr = SingleDimArray(length, dtype=dtype)
            arr.setslice(space,((0,length,1,length),) ,w_size_or_iterable)
        elif space.issequence_w(w_elem):
            shape = [len(l)]
            while space.issequence_w(w_elem):
                shape.append(space.len_w(w_elem))
                w_elem = space.getitem(w_elem, space.wrap(0))
            arr = NDimArray(shape, dtype=dtype)
            #Assign all the values
            arr.setslice(space,((0,length,1,length),) ,w_size_or_iterable)
        else:
            arr = SingleDimArray(length, dtype=dtype)
            i = 0
            for w_elem in l:
                dtype.setitem_w(space, arr.storage, i, w_elem)
                i += 1
        return arr

    def _unaryop_impl(ufunc_name):
        def impl(self, space):
            return getattr(interp_ufuncs.get(space), ufunc_name).call(space, [self])
        return func_with_new_name(impl, "unaryop_%s_impl" % ufunc_name)

    descr_pos = _unaryop_impl("positive")
    descr_neg = _unaryop_impl("negative")
    descr_abs = _unaryop_impl("absolute")

    def _binop_impl(ufunc_name):
        def impl(self, space, w_other):
            return getattr(interp_ufuncs.get(space), ufunc_name).call(space, [self, w_other])
        return func_with_new_name(impl, "binop_%s_impl" % ufunc_name)

    descr_add = _binop_impl("add")
    descr_sub = _binop_impl("subtract")
    descr_mul = _binop_impl("multiply")
    descr_div = _binop_impl("divide")
    descr_pow = _binop_impl("power")
    descr_mod = _binop_impl("mod")

    descr_eq = _binop_impl("equal")
    descr_ne = _binop_impl("not_equal")
    descr_lt = _binop_impl("less")
    descr_le = _binop_impl("less_equal")
    descr_gt = _binop_impl("greater")
    descr_ge = _binop_impl("greater_equal")

    def _binop_right_impl(ufunc_name):
        def impl(self, space, w_other):
            w_other = scalar_w(space,
                interp_ufuncs.find_dtype_for_scalar(space, w_other, self.find_dtype()),
                w_other
            )
            return getattr(interp_ufuncs.get(space), ufunc_name).call(space, [w_other, self])
        return func_with_new_name(impl, "binop_right_%s_impl" % ufunc_name)

    descr_radd = _binop_right_impl("add")
    descr_rsub = _binop_right_impl("subtract")
    descr_rmul = _binop_right_impl("multiply")
    descr_rdiv = _binop_right_impl("divide")
    descr_rpow = _binop_right_impl("power")
    descr_rmod = _binop_right_impl("mod")

    def _reduce_ufunc_impl(ufunc_name):
        def impl(self, space):
            return getattr(interp_ufuncs.get(space), ufunc_name).descr_reduce(space, self)
        return func_with_new_name(impl, "reduce_%s_impl" % ufunc_name)

    descr_sum = _reduce_ufunc_impl("add")
    descr_prod = _reduce_ufunc_impl("multiply")
    descr_max = _reduce_ufunc_impl("maximum")
    descr_min = _reduce_ufunc_impl("minimum")

    def _reduce_argmax_argmin_impl(op_name):
        reduce_driver = jit.JitDriver(greens=['signature'],
                         reds = ['i', 'size', 'result', 'self', 'cur_best', 'dtype'])
        def loop(self, size):
            result = 0
            cur_best = self.eval(0)
            i = 1
            dtype = self.find_dtype()
            while i < size:
                reduce_driver.jit_merge_point(signature=self.signature,
                                              self=self, dtype=dtype,
                                              size=size, i=i, result=result,
                                              cur_best=cur_best)
                new_best = getattr(dtype, op_name)(cur_best, self.eval(i))
                if dtype.ne(new_best, cur_best):
                    result = i
                    cur_best = new_best
                i += 1
            return result
        def impl(self, space):
            size = self.find_size()
            if size == 0:
                raise OperationError(space.w_ValueError,
                    space.wrap("Can't call %s on zero-size arrays" \
                            % op_name))
            return space.wrap(loop(self, size))
        return func_with_new_name(impl, "reduce_arg%s_impl" % op_name)

    def _all(self):
        size = self.find_size()
        dtype = self.find_dtype()
        i = 0
        while i < size:
            all_driver.jit_merge_point(signature=self.signature, self=self, dtype=dtype, size=size, i=i)
            if not dtype.bool(self.eval(i)):
                return False
            i += 1
        return True
    def descr_all(self, space):
        return space.wrap(self._all())

    def _any(self):
        size = self.find_size()
        dtype = self.find_dtype()
        i = 0
        while i < size:
            any_driver.jit_merge_point(signature=self.signature, self=self, size=size, dtype=dtype, i=i)
            if dtype.bool(self.eval(i)):
                return True
            i += 1
        return False
    def descr_any(self, space):
        return space.wrap(self._any())

    descr_argmax = _reduce_argmax_argmin_impl("max")
    descr_argmin = _reduce_argmax_argmin_impl("min")

    def descr_dot(self, space, w_other):
        w_other = convert_to_array(space, w_other)
        if isinstance(w_other, Scalar):
            return self.descr_mul(space, w_other)
        else:
            w_res = self.descr_mul(space, w_other)
            assert isinstance(w_res, BaseArray)
            return w_res.descr_sum(space)

    def _getnums(self, comma):
        dtype = self.find_dtype()
        if self.find_size() > 1000:
            nums = [
                dtype.str_format(self.eval(index))
                for index in range(3)
            ]
            nums.append("..." + "," * comma)
            nums.extend([
                dtype.str_format(self.eval(index))
                for index in range(self.find_size() - 3, self.find_size())
            ])
        else:
            nums = [
                dtype.str_format(self.eval(index))
                for index in range(self.find_size())
            ]
        return nums

    def get_concrete(self):
        raise NotImplementedError

    def descr_get_dtype(self, space):
        return space.wrap(self.find_dtype())

    def descr_get_shape(self, space):
        #return space.newtuple([self.descr_len(space)])
        return self.get_concrete().descr_shape(space)

    def descr_copy(self, space):
        return space.call_function(space.gettypefor(BaseArray), self, self.find_dtype())

    def descr_len(self, space):
        return self.get_concrete().descr_len(space)

    def descr_repr(self, space):
        return self.get_concrete().descr_repr(space)
        # Simple implementation so that we can see the array. Needs work.
        #concrete = self.get_concrete()
        #res = "array([" + ", ".join(concrete._getnums(False)) + "]"
        #dtype = concrete.find_dtype()
        #if (dtype is not space.fromcache(interp_dtype.W_Float64Dtype) and
        #    dtype is not space.fromcache(interp_dtype.W_Int64Dtype)) or not self.find_size():
        #    res += ", dtype=" + dtype.name
        #res += ")"
        #return space.wrap(res)

    def descr_str(self, space):
        # Simple implementation so that we can see the array. Needs work.
        concrete = self.get_concrete()
        return space.wrap("[" + " ".join(concrete._getnums(True)) + "]")
    def create_sssl_from_w_idx(self,space, w_idx):
        start_stop_step_length = []
        myshape = self.find_shape()
        #print 'descr_getitem(...,',w_idx,')'
        if space.isinstance_w(w_idx, space.w_tuple):
            length = space.len_w(w_idx)
            if length == 0:
                return space.wrap(self)
            if length>len(myshape):
                raise OperationError(space.w_IndexError,
                                     space.wrap("invalid index: cannot return %d array from % dim"%(length,len(myshape))))
            for i in range(length):
                w_idx1 = space.getitem(w_idx, space.wrap(i))
                if space.is_true(space.isinstance(w_idx1,space.w_int)) and space.is_true(space.gt(w_idx1,space.wrap(myshape[i]))):
                    raise OperationError(space.w_IndexError,
                             space.wrap("index (%d) out of range (0<=index<%d) in dimension %d"%(space.unwrap(w_idx1),myshape[i],i)))
                start, stop, step, slice_length = space.decode_index4(w_idx1, myshape[i])
                if step==0:
                    step = 1
                    stop = min(myshape[i],start+1)
                    slice_length = 1
                    #print 'got step==0, start=',start,'stop=',stop,'w_idx',w_idx,'shape',self.shape
                if start>=myshape[i]:
                    slice_length=0
                start_stop_step_length.append((start,stop,step,slice_length)) 
        else:
            start, stop, step, slice_length = space.decode_index4(w_idx, self.find_size())
            if step == 0:
                step = 1
                stop = min(myshape[0],start+1)
                slice_length = 1
                # Single index
            if start>=myshape[0]:
                slice_length=0
            start_stop_step_length.append((start,stop,step,slice_length)) 
        return start_stop_step_length
    def descr_getitem(self, space, w_idx):
        start_stop_step_length = self.create_sssl_from_w_idx(space,w_idx)
        # Slice
        slc = self.slice_type
        new_sig = signature.Signature.find_sig([ slc.signature, self.signature ])
        res = slc(start_stop_step_length, self, new_sig)
        return space.wrap(res)

    def descr_setitem(self, space, w_idx, w_value):
        self.invalidated()
        start_stop_step_length = self.create_sssl_from_w_idx(space,w_idx)
        concrete = self.get_concrete()
        if isinstance(w_value, BaseArray):
            # for now we just copy if setting part of an array from
            # part of itself. can be improved.
            if (concrete.get_root_storage() ==
                w_value.get_concrete().get_root_storage()):
                w_value = space.call_function(space.gettypefor(BaseArray), w_value)
                assert isinstance(w_value, BaseArray)
        else:
            w_value = convert_to_array(space, w_value)
        concrete.setslice(space, start_stop_step_length, w_value)

    def descr_mean(self, space):
        return space.wrap(space.float_w(self.descr_sum(space))/self.find_size())

    def _sliceloop(self, start, stop, step, source, dest):
        i = start
        j = 0
        while (step > 0 and i < stop) or (step < 0 and i > stop):
            slice_driver.jit_merge_point(signature=source.signature, step=step,
                                         stop=stop, i=i, j=j, source=source,
                                         dest=dest)
            dest.setitem(i, source.eval(j).convert_to(dest.find_dtype()))
            j += 1
            i += step

def convert_to_array(space, w_obj):
    if isinstance(w_obj, BaseArray):
        return w_obj
    elif space.issequence_w(w_obj):
        # Convert to array.
        w_obj = space.call_function(space.gettypefor(BaseArray), w_obj)
        assert isinstance(w_obj, BaseArray)
        return w_obj
    else:
        # If it's a scalar
        dtype = interp_ufuncs.find_dtype_for_scalar(space, w_obj)
        return scalar_w(space, dtype, w_obj)

def scalar_w(space, dtype, w_obj):
    return Scalar(dtype, dtype.unwrap(space, w_obj))

class Scalar(BaseArray):
    """
    Intermediate class representing a float literal.
    """
    signature = signature.BaseSignature()

    _attrs_ = ["dtype", "value"]

    def __init__(self, dtype, value):
        BaseArray.__init__(self)
        self.dtype = dtype
        self.value = value

    def find_size(self):
        raise ValueError

    def find_dtype(self):
        return self.dtype

    def eval(self, i):
        return self.value

class VirtualArray(BaseArray):
    """
    Class for representing virtual arrays, such as binary ops or ufuncs
    """
    def __init__(self, signature, res_dtype):
        BaseArray.__init__(self)
        self.forced_result = None
        self.signature = signature
        self.res_dtype = res_dtype

    def _del_sources(self):
        # Function for deleting references to source arrays, to allow garbage-collecting them
        raise NotImplementedError

    def compute(self):
        i = 0
        signature = self.signature
        result_shape = self.find_shape()
        if len(result_shape)<2:
            result_size = self.find_size()
            result = SingleDimArray(result_size, self.find_dtype())
            while i < result_size:
                numpy_driver.jit_merge_point(signature=signature,
                                         result_size=result_size, i=i,
                                         self=self, result=result)
                result.dtype.setitem(result.storage, i, self.eval(i))
                i += 1
        else:
            result = NDimArray(result_shape, self.find_dtype())
            result_size = self.find_size()
            while i < result_size:
                numpy_driver.jit_merge_point(signature=signature,
                                         result_size=result_size, i=i,
                                         self=self, result=result)
                result.dtype.setitem(result.storage, i, self.eval(i))
                i += 1
        return result

    def force_if_needed(self):
        if self.forced_result is None:
            self.forced_result = self.compute()
            self._del_sources()

    def get_concrete(self):
        self.force_if_needed()
        return self.forced_result

    def eval(self, i):
        if self.forced_result is not None:
            return self.forced_result.eval(i)
        return self._eval(i)

    def setitem(self, item, value):
        return self.get_concrete().setitem(item, value)

    def find_shape(self):
        if self.forced_result is not None:
            # The result has been computed and sources may be unavailable
            return self.forced_result.find_shape()
        return self._find_shape()

    def find_size(self):
        if self.forced_result is not None:
            # The result has been computed and sources may be unavailable
            return self.forced_result.find_size()
        return self._find_size()

    def find_dtype(self):
        return self.res_dtype


class Call1(VirtualArray):
    def __init__(self, signature, res_dtype, values):
        VirtualArray.__init__(self, signature, res_dtype)
        self.values = values

    def _del_sources(self):
        self.values = None

    def _find_shape(self):
        return self.values.find_shape()

    def _find_size(self):
        return self.values.find_size()

    def _find_dtype(self):
        return self.res_dtype

    def _eval(self, i):
        val = self.values.eval(i).convert_to(self.res_dtype)

        sig = jit.promote(self.signature)
        assert isinstance(sig, signature.Signature)
        call_sig = sig.components[0]
        assert isinstance(call_sig, signature.Call1)
        return call_sig.func(self.res_dtype, val)

class Call2(VirtualArray):
    """
    Intermediate class for performing binary operations.
    """
    def __init__(self, signature, calc_dtype, res_dtype, left, right):
        VirtualArray.__init__(self, signature, res_dtype)
        self.left = left
        self.right = right
        self.calc_dtype = calc_dtype

    def _del_sources(self):
        self.left = None
        self.right = None

    def _find_shape(self):
        try:
            return self.left.find_shape()
        except ValueError:
            pass
        return self.right.find_shape()

    def _find_size(self):
        try:
            return self.left.find_size()
        except ValueError:
            pass
        return self.right.find_size()

    def _eval(self, i):
        lhs = self.left.eval(i).convert_to(self.calc_dtype)
        rhs = self.right.eval(i).convert_to(self.calc_dtype)

        sig = jit.promote(self.signature)
        assert isinstance(sig, signature.Signature)
        call_sig = sig.components[0]
        assert isinstance(call_sig, signature.Call2)
        return call_sig.func(self.calc_dtype, lhs, rhs)

class ViewArray(BaseArray):
    """
    Class for representing views of arrays, they will reflect changes of parent
    arrays. Example: slices
    """
    def __init__(self, parent, signature):
        BaseArray.__init__(self)
        self.signature = signature
        self.parent = parent
        self.invalidates = parent.invalidates

    def get_concrete(self):
        # in fact, ViewArray never gets "concrete" as it never stores data.
        # This implementation is needed for BaseArray getitem/setitem to work,
        # can be refactored.
        self.parent.get_concrete()
        return self

    def eval(self, i):
        return self.parent.eval(self.calc_index(i))

    @unwrap_spec(item=int)
    def setitem_w(self, space, item, w_value):
        return self.parent.setitem_w(space, self.calc_index(item), w_value)

    def setitem(self, item, value):
        # This is currently not possible to be called from anywhere.
        raise NotImplementedError

    def descr_len(self, space):
        return space.wrap(self.find_size())

    def calc_index(self, item):
        raise NotImplementedError

class SingleDimSlice(ViewArray):
    signature = signature.BaseSignature()

    def __init__(self, start_stop_step_length , parent, signature):
        ViewArray.__init__(self, parent, signature)
        start, stop, step, slice_length = start_stop_step_length[0]
        if isinstance(parent, SingleDimSlice):
            self.start = parent.calc_index(start)
            self.stop = parent.calc_index(stop)
            self.step = parent.step * step
            self.parent = parent.parent
        else:
            self.start = start
            self.stop = stop
            self.step = step
            self.parent = parent
        self.size = slice_length

    def get_root_storage(self):
        return self.parent.get_concrete().get_root_storage()

    def find_shape(self):
        return (self.size,)

    def find_size(self):
        return self.size

    def find_dtype(self):
        return self.parent.find_dtype()

    def descr_shape(self,space):
        return space.newtuple([space.wrap(self.size)])

    def setslice(self, start_stop_step_length, arr):
        space, start, stop, step, slice_length = start_stop_step_length[0]
        start = self.calc_index(start)
        if stop != -1:
            stop = self.calc_index(stop)
        step = self.step * step
        self._sliceloop(start, stop, step, arr, self.parent)

    def calc_index(self, item):
        return (self.start + item * self.step)

    def descr_repr(self, space):
        # Simple implementation so that we can see the array. Needs work.
        concrete = self.get_concrete()
        res = "array([" + ", ".join(concrete._getnums(False)) + "]"
        dtype = concrete.find_dtype()
        if (dtype is not space.fromcache(interp_dtype.W_Float64Dtype) and
            dtype is not space.fromcache(interp_dtype.W_Int64Dtype)) or not self.find_size():
            res += ", dtype=" + dtype.name
        res += ")"
        return space.wrap(res)
SingleDimSlice.slice_type = SingleDimSlice
class NDimSlice(ViewArray):
    signature = signature.BaseSignature()
    #start, stop,step,slice_length are lists
    def __init__(self, start_stop_step_length, parent, signature):
        #print 'NDimSlice::init(',start_stop_step_length,',...)'
        ViewArray.__init__(self, parent, signature)
        self.start = []
        self.stop  = []
        self.step  = [] 
        self.shape = []
        self.realdims=[]
        i=0
        if isinstance(parent, NDimSlice):
            self.parent = parent.parent
            for sssl in start_stop_step_length:
                start,stop,step,slice_length = sssl
                self.start.append(parent.start[i]+start*parent.step[i])
                self.stop.append(min(parent.shape[i], parent.stop[i]+stop*parent.step[i]))
                self.step.append(step*parent.step[i])
                self.shape.append(slice_length)
                if slice_length>1:
                    self.realdims.append(i)
                i+=1
        else:
            self.parent = parent
            for sssl in start_stop_step_length:
                start,stop,step,slice_length = sssl
                self.start.append(start)
                self.stop.append(stop)
                self.step.append(step)
                self.shape.append(slice_length)
                if slice_length>1:
                    self.realdims.append(i)
                i+=1
        for ii in range(i,len(parent.shape)):
            self.start.append(0)
            self.stop.append(parent.shape[ii])
            self.step.append(1)
            self.shape.append(parent.shape[ii])
            if parent.shape[ii]>1:
                self.realdims.append(ii)
        self.size = prod(self.shape)
        self.signature = signature

    def get_root_storage(self):
        return self.parent.get_concrete().get_root_storage()

    def find_shape(self):
        return self.shape

    def find_size(self):
        return self.size

    def find_dtype(self):
        return self.parent.find_dtype()

    def descr_shape(self,space):
        return space.wrap(self.shape)

    def eval(self, i):
        return self.parent.eval(self.calc_index((i,)))

    def setslice(self, space, start_stop_step_length, arr):
        sig = signature.Signature.find_sig([ self.signature, self.signature ])
        NDimSlice(start_stop_step_length, self, sig).setvals(space, arr)

    def setvals(self,space, arr):
        sig = signature.Signature.find_sig([ self.signature, self.signature ])
        if len(self.realdims)==0:
            self.parent.setitem(self.calc_index([]),arr.eval(0).convert_to(self.parent.find_dtype()))
        elif len(self.realdims)==1:
            rd = self.realdims[0]
            i=0
            count = self.shape[rd]
            dest = self.parent
            source = arr
            while( i < count):
                nslice_driver.jit_merge_point(signature=self.signature, 
                                         count=count, i=i, source=source,
                                         self=self, dest=dest)
                dest.setitem(self.calc_index((i,)), source.eval(i).convert_to(dest.find_dtype()))
                i += 1
        else: 
            for rd in self.realdims:
                for i in range(self.shape[rd]):
                    subarr = NDimSlice(((i,i+1,1,1),) , self, sig)
                    subarr.setvals(space, arr.descr_getitem(space,space.wrap(i)))
                    
    def calc_index(self, item):
        indx=0
        assert len(item)==len(self.realdims),'NDimSlice::calc_index( %s ) does not match %s'%(str(item),str(self.realdims))
        for i in range(len(self.shape)):
            if i in self.realdims:
                indx += self.start[i] + item[self.realdims.index(i)]*self.step[i]
            else:
                indx += self.start[i]
            if i+1<len(self.shape):
               indx *= self.parent.shape[i+1]
            #print 'NDimSlice::calc_index(',item,') =>',indx,'after',i,'itterations'
        return indx
    def tostr(self):
        #print 'NDimSlice::tostr, self.shape=',self.shape,'ndims=',self.realdims
        res=''
        if len(self.realdims)>2:
            #Find first real dimension
            start_stop_step_length=[]
            for d,n in enumerate(self.shape):
                start_stop_step_length.append((0,1,1,1))
                if n>1:
                    break
            res += '['
            for i in range(n):
                res += NDimSlice(start_stop_step_length+[i,i+1,1,1],self,self.signature).tostr()
            res += ']'
        elif len(self.realdims)==2:
            dtype = self.find_dtype()
            res += '['
            for i in range(self.start[self.realdims[0]],
                           self.stop[self.realdims[0]],
                           self.step[self.realdims[0]]):
                if i>0:
                    res += '         '
                res +='['
                res += ', '.join([ dtype.str_format(self.parent.eval(self.calc_index((i,j)))) \
                          for j in range(self.start[self.realdims[1]],
                               self.stop[self.realdims[1]],
                               self.step[self.realdims[1]])])
                res+= ']\n'
            res = res[:-1] + ']'
        elif len(self.realdims)==1:
            dtype = self.find_dtype()
            res += '['
            res += ', '.join([dtype.str_format(self.parent.eval(self.calc_index((i,)))) \
                      for i in range(self.start[self.realdims[0]],
                           self.stop[self.realdims[0]],
                           self.step[self.realdims[0]])])
            res += ']'
        elif prod(self.shape)==1:
            dtype = self.find_dtype()
            res += '[' + dtype.str_format(self.parent.eval(self.calc_index([]))) + ']'
        else:
            print 'NDimSlice::tostr, self.shape=',self.shape,'realdims=',self.realdims,',start=',self.start,',stop=',self.stop
            res += 'empty'
        return res    
    def descr_repr(self, space):
        # Simple implementation so that we can see the array. Needs work.
        concrete = self.get_concrete()
        res = "ndarray(" + self.tostr() 
        dtype = concrete.find_dtype()
        if (dtype is not space.fromcache(interp_dtype.W_Float64Dtype) and
            dtype is not space.fromcache(interp_dtype.W_Int64Dtype)) or not self.find_size():
            res += ", dtype=" + dtype.name
        res += ")"
        return space.wrap(res)
NDimSlice.slice_type = NDimSlice

class SingleDimArray(BaseArray):
    slice_type = SingleDimSlice
    def __init__(self, size, dtype):
        BaseArray.__init__(self)
        self.size = size
        self.dtype = dtype
        self.storage = dtype.malloc(size)
        self.signature = dtype.signature

    def get_concrete(self):
        return self

    def get_root_storage(self):
        return self.storage

    def find_shape(self):
        return (self.size,)

    def find_size(self):
        return self.size

    def find_dtype(self):
        return self.dtype

    def eval(self, i):
        return self.dtype.getitem(self.storage, i)

    def descr_shape(self, space):
        return space.newtuple([space.wrap(self.size)])

    def descr_len(self, space):
        return space.wrap(self.size)

    def setitem_w(self, space, item, w_value):
        self.invalidated()
        self.dtype.setitem_w(space, self.storage, item, w_value)

    def setitem(self, item, value):
        self.invalidated()
        self.dtype.setitem(self.storage, item, value)

    def setslice(self, space, start_stop_step_length, arr):
        start, stop, step, slice_length = start_stop_step_length[0]
        self._sliceloop(start, stop, step, arr, self)

    def descr_repr(self, space):
        # Simple implementation so that we can see the array. Needs work.
        concrete = self.get_concrete()
        res = "array([" + ", ".join(concrete._getnums(False)) + "]"
        dtype = concrete.find_dtype()
        if (dtype is not space.fromcache(interp_dtype.W_Float64Dtype) and
            dtype is not space.fromcache(interp_dtype.W_Int64Dtype)) or not self.find_size():
            res += ", dtype=" + dtype.name
        res += ")"
        return space.wrap(res)

    def __del__(self):
        lltype.free(self.storage, flavor='raw', track_allocation=False)

class NDimArray(BaseArray):
    slice_type = NDimSlice
    def __init__(self, shape, dtype):
        BaseArray.__init__(self)
        self.size = prod(shape)
        self.shape = shape
        self.dtype = dtype
        self.storage = dtype.malloc(self.size)
        #print 'Creating NDimArray(',shape,',...)'
        self.signature = dtype.signature

    def get_concrete(self):
        return self

    def get_root_storage(self):
        return self.storage

    def find_shape(self):
        return self.shape

    def find_size(self):
        return self.size

    def find_dtype(self):
        return self.dtype

    def eval(self, i):
        try:
            return self.dtype.getitem(self.storage, i)
        except:
            print 'NDimArray::eval(',i,')'
            return self.dtype.getitem(self.storage, i)

    def descr_shape(self, space):
        return space.wrap(self.shape)

    def descr_len(self, space):
        return space.wrap(self.size)

    def setitem_w(self, space, item, w_value):
        self.invalidated()
        self.dtype.setitem_w(space, self.storage, item, w_value)

    def setitem(self, item, value):
        self.invalidated()
        self.dtype.setitem(self.storage, item, value)

    def setslice(self, space, start_stop_step_length, arr):
        sig = signature.Signature.find_sig([ self.signature, self.signature ])
        NDimSlice(start_stop_step_length, self, sig).setvals(space, arr)

    def descr_repr(self, space):
        # Simple implementation so that we can see the array. Needs work.
        concrete = self.get_concrete()
        new_sig = signature.Signature.find_sig([ NDimSlice.signature, self.signature ])
        res = "ndarray(" + \
        NDimSlice((), self, new_sig).tostr() \
        + ',' 
        dtype = concrete.find_dtype()
        if (dtype is not space.fromcache(interp_dtype.W_Float64Dtype) and
            dtype is not space.fromcache(interp_dtype.W_Int64Dtype)) or not self.find_size():
            res += ", dtype=" + dtype.name
        res += ")"
        return space.wrap(res)

    def __del__(self):
        lltype.free(self.storage, flavor='raw', track_allocation=False)


def zeros(space, w_size, w_dtype=None):
    dtype = space.interp_w(interp_dtype.W_Dtype,
        space.call_function(space.gettypefor(interp_dtype.W_Dtype), w_dtype)
    )
    if space.is_true(space.isinstance(w_size,space.w_int)):
        return space.wrap(SingleDimArray(space.unwrap(w_size), dtype=dtype))
    else:
        size = [space.unwrap(s) for s in space.listview(w_size)]
        return space.wrap(NDimArray(size, dtype=dtype))

@unwrap_spec(size=int)
def ones(space, size, w_dtype=None):
    dtype = space.interp_w(interp_dtype.W_Dtype,
        space.call_function(space.gettypefor(interp_dtype.W_Dtype), w_dtype)
    )

    arr = SingleDimArray(size, dtype=dtype)
    one = dtype.adapt_val(1)
    for i in xrange(size):
        arr.dtype.setitem(arr.storage, i, one)
    return space.wrap(arr)

BaseArray.typedef = TypeDef(
    'numarray',
    __new__ = interp2app(BaseArray.descr__new__.im_func),


    __len__ = interp2app(BaseArray.descr_len),
    __getitem__ = interp2app(BaseArray.descr_getitem),
    __setitem__ = interp2app(BaseArray.descr_setitem),

    __pos__ = interp2app(BaseArray.descr_pos),
    __neg__ = interp2app(BaseArray.descr_neg),
    __abs__ = interp2app(BaseArray.descr_abs),

    __add__ = interp2app(BaseArray.descr_add),
    __sub__ = interp2app(BaseArray.descr_sub),
    __mul__ = interp2app(BaseArray.descr_mul),
    __div__ = interp2app(BaseArray.descr_div),
    __pow__ = interp2app(BaseArray.descr_pow),
    __mod__ = interp2app(BaseArray.descr_mod),

    __radd__ = interp2app(BaseArray.descr_radd),
    __rsub__ = interp2app(BaseArray.descr_rsub),
    __rmul__ = interp2app(BaseArray.descr_rmul),
    __rdiv__ = interp2app(BaseArray.descr_rdiv),
    __rpow__ = interp2app(BaseArray.descr_rpow),
    __rmod__ = interp2app(BaseArray.descr_rmod),

    __eq__ = interp2app(BaseArray.descr_eq),
    __ne__ = interp2app(BaseArray.descr_ne),
    __lt__ = interp2app(BaseArray.descr_lt),
    __le__ = interp2app(BaseArray.descr_le),
    __gt__ = interp2app(BaseArray.descr_gt),
    __ge__ = interp2app(BaseArray.descr_ge),

    __repr__ = interp2app(BaseArray.descr_repr),
    __str__ = interp2app(BaseArray.descr_str),

    dtype = GetSetProperty(BaseArray.descr_get_dtype),
    shape = GetSetProperty(BaseArray.descr_get_shape),

    mean = interp2app(BaseArray.descr_mean),
    sum = interp2app(BaseArray.descr_sum),
    prod = interp2app(BaseArray.descr_prod),
    max = interp2app(BaseArray.descr_max),
    min = interp2app(BaseArray.descr_min),
    argmax = interp2app(BaseArray.descr_argmax),
    argmin = interp2app(BaseArray.descr_argmin),
    all = interp2app(BaseArray.descr_all),
    any = interp2app(BaseArray.descr_any),
    dot = interp2app(BaseArray.descr_dot),

    copy = interp2app(BaseArray.descr_copy),
)
