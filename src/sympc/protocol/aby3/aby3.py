"""ABY3 Protocol.

ABY3 : A Mixed Protocol Framework for Machine Learning.
https://eprint.iacr.org/2018/403.pdf
"""
# stdlib
from typing import Any
from typing import List
from typing import Tuple

# third party
import torch
import torchcsprng as csprng

from sympc.config import Config
from sympc.protocol.protocol import Protocol
from sympc.session import Session
from sympc.tensor import MPCTensor
from sympc.tensor import PRIME_NUMBER
from sympc.tensor import ReplicatedSharedTensor
from sympc.tensor.tensor import SyMPCTensor
from sympc.utils import get_type_from_ring

gen = csprng.create_random_device_generator()


class ABY3(metaclass=Protocol):
    """ABY3 Protocol Implementation."""

    """Used for Share Level static operations like distributing the shares."""
    share_class: SyMPCTensor = ReplicatedSharedTensor
    security_levels: List[str] = ["semi-honest", "malicious"]

    def __init__(self, security_type: str = "semi-honest"):
        """Initialization of the Protocol.

        Args:
            security_type : specifies the security level of the Protocol.

        Raises:
            ValueError : If invalid security_type is provided.
        """
        if security_type not in self.security_levels:
            raise ValueError(f"{security_type} is not a valid security type")

        self.security_type = security_type

    def __eq__(self, other: Any) -> bool:
        """Check if "self" is equal with another object given a set of attributes to compare.

        Args:
            other (Any): Object to compare

        Returns:
            bool: True if equal False if not.
        """
        if self.security_type != other.security_type:
            return False

        if type(self) != type(other):
            return False

        return True

    @staticmethod
    def truncation_algorithm1(
        ptr_list: List[torch.Tensor],
        shape: torch.Size,
        session: Session,
        ring_size: int,
        config: Config,
    ) -> List[ReplicatedSharedTensor]:
        """Performs the ABY3 truncation algorithm1.

        Args:
            ptr_list (List[torch.Tensor]): Tensors to truncate
            shape (torch.Size) : shape of tensor values
            session (Session) : session the tensor belong to
            ring_size (int): Ring size of the underlying tensors.
            config (Config): The configuration(base,precision) of the underlying tensors.

        Returns:
            List["ReplicatedSharedTensor"] : Truncated shares.
        """
        tensor_type = get_type_from_ring(ring_size)
        rand_value = torch.empty(size=shape, dtype=tensor_type).random_(generator=gen)
        base = config.encoder_base
        precision = config.encoder_precision
        scale = base ** precision
        x1, x2, x3 = ptr_list
        x1_trunc = x1 >> precision if base == 2 else x1 // scale
        x_trunc = (x2 + x3) >> precision if base == 2 else (x2 + x3) // scale
        shares = [x1_trunc, x_trunc - rand_value, rand_value]
        ptr_list = ReplicatedSharedTensor.distribute_shares(
            shares, session, ring_size, config
        )
        return ptr_list

    @staticmethod
    def truncate(
        x: MPCTensor, session: Session, ring_size: int, config: Config
    ) -> MPCTensor:
        """Perfoms the ABY3 truncation algorithm.

        Args:
            x (MPCTensor): input tensor
            session (Session) : session of the input tensor.
            ring_size (int): Ring size of the underlying tensor.
            config (Config) : The configuration(base,precision) of the underlying tensor.

        Returns:
            MPCTensor: truncated MPCTensor.

        Raises:
            ValueError : parties involved in the computation is not equal to three.
            ValueError : Invalid MPCTensor share pointers.

        TODO :Switch to trunc2 algorithm  as it is communication efficient.
        """
        if session.nr_parties != 3:
            raise ValueError("Share truncation algorithm 1 works only for 3 parites.")

        # RSPointer - public ops, Tensor Pointer - Private ops
        ptr_list = []
        ptr_name = x.share_ptrs[0].__name__

        # TODO:Shoud be concised,lot of branching done,to improve communication efficiency.

        if ptr_name == "ReplicatedSharedTensorPointer":

            if ring_size in {2, PRIME_NUMBER}:
                share_ptrs = x.share_ptrs
            else:
                ptr_list.append(x.share_ptrs[0].get_shares()[0].get_copy())
                ptr_list.extend(x.share_ptrs[1].get_copy().shares)
                share_ptrs = ABY3.truncation_algorithm1(
                    ptr_list, x.shape, session, ring_size, config
                )

        elif ptr_name == "TensorPointer":
            ptr_list = [share.get_copy() for share in x.share_ptrs]

            if ring_size in {2, PRIME_NUMBER}:
                share_ptrs = ReplicatedSharedTensor.distribute_shares(
                    ptr_list, session, ring_size, config
                )

            else:

                share_ptrs = ABY3.truncation_algorithm1(
                    ptr_list, x.shape, session, ring_size, config
                )
        else:
            raise ValueError("{ptr_name} not supported.")

        result = MPCTensor(shares=share_ptrs, session=session, shape=x.shape)

        return result

    @staticmethod
    def truncation_algorithm2(x: MPCTensor, session: Session) -> MPCTensor:
        """Truncates the MPCTensor by scale factor using trunc2 algorithm.

        Args:
            x (MPCTensor): input tensor
            session (Session) : session of the input tensor.

        Returns:
            MPCTensor: truncated MPCTensor.

        TODO : The truncation algorithm 2 is erroneous, to be optimized.
        """
        r, rPrime = ABY3.get_truncation_pair(x, session)
        scale = session.config.encoder_base ** session.config.encoder_precision
        x_rp = x - rPrime
        x_rp = x_rp.reconstruct(decode=False) // scale
        zero = torch.tensor([0])
        x_rp = MPCTensor(shares=[x_rp, zero, zero], session=x.session, shape=x.shape)

        result = r + x_rp

        return result

    @staticmethod
    def _get_truncation_pair(x: MPCTensor, session: Session) -> Tuple[MPCTensor]:
        """Generates truncation pair for the given MPCTensor.

        Args:
            x (MPCTensor): input tensor
            session (Session) : session of the input tensor.

        Returns:
            Tuple[MPCTensor]: generated truncation pair.
        """
        r: List = []
        rPrime: List = []
        for session_ptrs in session.session_ptrs:
            rst = session_ptrs.prrs_generate_random_share(shape=x.shape)
            rst = rst.resolve_pointer_type()
            rPrime.append(rst)
            r.append(rst)

        r = ABY3.truncation_algorithm1(r, x.shape, session)
        r_mpc = MPCTensor(shares=r, session=session, shape=x.shape)
        rPrime_mpc = MPCTensor(shares=rPrime, session=session, shape=x.shape)
        return r_mpc, rPrime_mpc
