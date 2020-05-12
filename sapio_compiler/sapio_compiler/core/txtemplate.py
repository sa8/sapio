from __future__ import annotations

from typing import Any, Dict, List, Tuple

import sapio_compiler.contract
import sapio_compiler.core.bindable_contract
from bitcoinlib.messages import COutPoint, CScript, CTransaction, CTxIn, CTxOut
from bitcoinlib.static_types import Amount, LockTime, Sequence, Version, uint32
from sapio_compiler.core.analysis.funds import HasEnoughFunds, WithinFee
import struct
import hashlib


class MetaDataContainer:
    """
    MetaDataContainer exists to hold content on a per-output basis
    """

    def __init__(self, color: str, label: str) -> None:
        self.color: str = color
        self.label: str = label

    def to_json(self) -> Dict[str, str]:
        return {
            "color": self.color,
            "label": self.label,
        }


class TransactionTemplate:
    """
    TransactionTemplate is a subset of transaction fields that are relevant to
    computing a CheckTemplateVerify hash.

    Also holds onto metadata for the transaction and outputs.
    """

    __slots__ = [
        "n_inputs",
        "sequences",
        "outputs",
        "version",
        "lock_time",
        "outputs_metadata",
        "label",
    ]

    def __init__(self) -> None:
        self.n_inputs: int = 0
        self.sequences: List[Sequence] = [Sequence(uint32(0))]
        self.outputs: List[
            Tuple[Amount, sapio_compiler.core.bindable_contract.BindableContract[Any]]
        ] = []
        self.outputs_metadata: List[MetaDataContainer] = []
        self.version: Version = Version(uint32(2))
        self.lock_time: LockTime = LockTime(uint32(0))
        self.label: str = ""

    def to_json(self) -> Dict[str, Any]:
        return {
            "n_inputs": self.n_inputs,
            "sequences": self.sequences,
            "version": self.version,
            "lock_time": self.lock_time,
            "label": self.label,
            "outputs": [(amt, contract.to_json()) for (amt, contract) in self.outputs],
            "outputs_metadata": [o.to_json() for o in self.outputs_metadata],
        }

    def get_ctv_hash(self) -> bytes:
        """
        returns the standard template hash for this txtemplate assuming that the input will be spent
        at index 0.
        """
        # Implicitly always at index 0!
        return self.get_standard_template_hash(0)

    # TODO: Add safety mechanisms here
    def set_sequence(self, sequence: Sequence, idx: int = 0) -> None:
        """
        sets a sequence for the first input, or another if specified.
        Not bounds checked. Most of the time a txtemplate will have just 1 input.
        """
        self.sequences[idx] = sequence

    def set_locktime(self, sequence: LockTime) -> None:
        """
        sets the locktime for the entire transaction
        """
        self.lock_time = sequence

    def get_base_transaction(self) -> CTransaction:
        """
        casts the transaction template to a CTransaction for general use
        """
        tx = CTransaction()
        tx.nVersion = self.version
        tx.nLockTime = self.lock_time
        tx.vin = [CTxIn(None, CScript(), sequence) for sequence in self.sequences]
        tx.vout = [
            CTxOut(a, b.witness_manager.get_p2wsh_script()) for (a, b) in self.outputs
        ]
        return tx

    def bind_tx(self, point: COutPoint) -> CTransaction:
        """
        Binds a tx template (with a single input) to a specific
        COutPoint and returns a CTransaction.

        Rehash is called before the CTransaction is returned

        """
        tx = self.get_base_transaction()
        tx.vin[0].prevout = point
        tx.rehash()
        return tx

    def get_standard_template_hash(self, nIn: int) -> bytes:
        """
        computes the standard template hash for a given input index

        is computed equivalently to bitcoinlib.messages version, but is "inlined" to avoid
        performance issueS
        """
        ret = hashlib.sha256()
        ret.update(struct.pack("<i", self.version))
        ret.update(struct.pack("<I", self.lock_time))
        # TODO: Reinstate if adding non-segwit input support
        # if any(inp.scriptSig for inp in self.vin):
        #    r += sha256(b"".join(ser_string(inp.scriptSig) for inp in self.vin))
        ret.update(struct.pack("<I", self.n_inputs))
        seqs_h = hashlib.sha256()
        for seq in self.sequences:
            seqs_h.update(struct.pack("<I", seq))
        ret.update(seqs_h.digest())
        ret.update(struct.pack("<I", len(self.outputs)))

        outs_h = hashlib.sha256()
        for (amt, contract) in self.outputs:
            outs_h.update(CTxOut(amt, contract.witness_manager.get_p2wsh_script()).serialize())
        ret.update(outs_h.digest())
        ret.update(struct.pack("<I", nIn))
        return ret.digest()

    def add_output(
        self,
        amount: Amount,
        contract: sapio_compiler.core.bindable_contract.BindableContract[Any],
    ) -> None:
        """
        Adds an output to a tx template. Checks that the amount is sufficient and that fees won't be
        burned by this output.
        """
        WithinFee(contract, amount)
        HasEnoughFunds(contract, amount)
        self.outputs.append((amount, contract))
        self.outputs_metadata.append(
            MetaDataContainer(
                contract.MetaData.color(contract), contract.MetaData.label(contract)
            )
        )

    def total_amount(self) -> Amount:
        return Amount(sum(a for (a, _) in self.outputs))
