import os
import typing
import warnings
from dataclasses import dataclass
from copy import deepcopy
from numbers import Real
import numpy

from tequila import BitString, QCircuit, TequilaException,Variable,compile_circuit
from tequila.circuit import gates
try:
    from openfermion.ops.representations import get_active_space_integrals  # needs openfermion 1.3
except ImportError as E:
    raise TequilaException("{}\nplease update openfermion to version 1.3 or higher".format(str(E)))

@dataclass
class ActiveSpaceData:
    """
    Small dataclass to keep the overview in active spaces
    Class is used internally
    """
    active_orbitals: list = None # active orbitals (spatial, c1)
    reference_orbitals: list = None  # reference orbitals (spatial, c1)

    def __str__(self):
        result = "Active Space Data:\n"
        result += "{key:15} : {value:15} \n".format(key="active_orbitals", value=str(self.active_orbitals))
        result += "{key:15} : {value:15} \n".format(key="reference_orbitals", value=str(self.reference_orbitals))
        result += "{key:15} : {value:15} \n".format(key="active_reference_orbitals",
                                                    value=str(self.active_reference_orbitals))
        return result

    @property
    def frozen_reference_orbitals(self):
        return [i for i in self.reference_orbitals if i not in self.active_orbitals]

    @property
    def active_reference_orbitals(self):
        return [i for i in self.reference_orbitals if i in self.active_orbitals]


class FermionicGateImpl(gates.QubitExcitationImpl):
    """
    Small helper class for Fermionic Excictation Gates
    Mainly so that "FermionicGate is displayed when circuits are printed
    """

    def __init__(self, generator, p0, transformation, indices=None, *args, **kwargs):
        super().__init__(generator=generator, target=generator.qubits, p0=p0, *args, **kwargs)
        self._name = "FermionicExcitation"
        self.transformation = transformation
        self.indices = indices
        if not hasattr(indices[0],"__len__"):
            self.indices = [(indices[2 * i], indices[2 * i+1]) for i in range(len(indices) // 2)]
        self.sign = self.format_excitation_variables(self.indices)
        self.indices = self.format_excitation_indices(self.indices)
    def compile(self, *args, **kwargs):
        if self.is_convertable_to_qubit_excitation():
            target = []
            for x in self.indices:
                for y in x:
                    target.append(y)
            return gates.QubitExcitation(target=target, angle=self.parameter, control=self.control)
        else:
            if self.transformation.lower().strip("_") == "jordanwigner":
                return self.fermionic_excitation(angle=self.sign*self.parameter, indices=self.indices, control=self.control,opt=False)
            else:
                return gates.Trotterized(generator=self.generator, control=self.control, angle=self.parameter, steps=1)
    def format_excitation_indices(self, idx):
        """
        Consistent formatting of excitation indices
        idx = [(p0,q0),(p1,q1),...,(pn,qn)]
        sorted as: p0<p1<pn and pi<qi
        :param idx: list of index tuples describing a single(!) fermionic excitation
        :return: list of index tuples
        """

        idx = [tuple(sorted(x)) for x in idx]
        idx = sorted(idx, key=lambda x: x[0])
        return list(idx)
    def format_excitation_variables(self, idx):
        """
        Consistent formatting of excitation variable
        idx = [(p0,q0),(p1,q1),...,(pn,qn)]
        sorted as: pi<qi and p0 < p1 < p2
        :param idx: list of index tuples describing a single(!) fermionic excitation
        :return: sign of the variable with re-ordered indices
        """
        sig = 1
        for pair in idx:
            if pair[1]>pair[0]:
                sig *= -1
        for pair in range(len(idx)-1):
            if idx[pair+1][0]>idx[pair][0]:
                sig *= -1
        return sig
    def cCRy(self, target: int, dcontrol: typing.Union[list, int], control: typing.Union[list, int],
             angle: typing.Union[Real, Variable, typing.Hashable], case: int = 1) -> QCircuit:
        '''
        Compilation of CRy as on https://doi.org/10.1103/PhysRevA.102.062612
        If not control passed, Ry returned
        Parameters
        ----------
        case: if 1 employs eq. 12 from the paper, if 0 eq. 13
        '''
        if control is not None and not len(control):
            control = None
        if isinstance(dcontrol, int):
            dcontrol = [dcontrol]
        if not len(dcontrol):
            return compile_circuit(gates.Ry(angle=angle, target=target, control=control))
        else:
            if isinstance(angle, str):
                angle = Variable(angle)
            U = QCircuit()
            aux = dcontrol[0]
            ctr = deepcopy(dcontrol)
            ctr.pop(0)
            if case:
                U += self.cCRy(target=target, dcontrol=ctr, angle=angle / 2, case=1, control=control) + gates.H(
                    aux) + gates.CNOT(target, aux)
                U += self.cCRy(target=target, dcontrol=ctr, angle=-angle / 2, case=0, control=control) + gates.CNOT(
                    target, aux) + gates.H(aux)
            else:
                U += gates.H(aux) + gates.CNOT(target, aux) + self.cCRy(target=target, dcontrol=ctr, angle=-angle / 2,
                                                                        case=0, control=control)
                U += gates.CNOT(target, aux) + gates.H(aux) + self.cCRy(target=target, dcontrol=ctr, angle=angle / 2,
                                                                        case=1, control=control)
            return U

    def fermionic_excitation(self, angle: typing.Union[Real, Variable, typing.Hashable], indices: typing.List,
                             control: typing.Union[int, typing.List] = None, opt: bool = True) -> QCircuit:
        '''
            Excitation [(i,j),(k,l)],... compiled following https://doi.org/10.1103/PhysRevA.102.062612
            opt: whether to optimized CNOT H CNOT --> Rz Rz CNOT Rz
        '''
        lto = []
        lfrom = []
        if isinstance(indices,tuple) and not hasattr(indices[0],"__len__"):
            indices = [(indices[2 * i], indices[2 * i + 1]) for i in range(len(indices) // 2)]
        for pair in indices:
            lfrom.append(pair[0])
            lto.append(pair[1])
        Upair = QCircuit()
        if isinstance(angle, str) or isinstance(angle, tuple):
            angle = Variable(angle)
        for i in range(len(lfrom) - 1):
            Upair += gates.CNOT(lfrom[i + 1], lfrom[i])
            Upair += gates.CNOT(lto[i + 1], lto[i])
            Upair += gates.X(lto[i]) + gates.X(lfrom[i])
        Upair += gates.CNOT(lto[-1], lfrom[-1])
        crt = lfrom[::-1] + lto
        Uladder = QCircuit()
        pairs = lfrom + lto
        pairs.sort()
        orbs = []
        for o in range(len(pairs) // 2):
            orbs += [*range(pairs[2 * o] + 1, pairs[2 * o + 1])]
        if len(orbs):
            for o in range(len(orbs) - 1):
                Uladder += gates.CNOT(orbs[o], orbs[o + 1])
            Uladder += compile_circuit(gates.CZ(orbs[-1], lto[-1]))
        crt.pop(-1)
        if control is not None and (isinstance(control, int) or len(control) == 1):
            if isinstance(control, int):
                crt.append(control)
            else:
                crt = crt + control
            control = []
        Ur = self.cCRy(target=lto[-1], dcontrol=crt, angle=angle, control=control)
        Upair2 = Upair.dagger()
        if opt:
            Ur.gates.pop(-1)
            Ur.gates.pop(-1)
            Upair2.gates.pop(0)
            Ur += gates.Rz(numpy.pi / 2, target=lto[-1]) + gates.Rz(-numpy.pi / 2, target=lfrom[-1])
            Ur += gates.CNOT(lto[-1], lfrom[-1]) + gates.Rz(numpy.pi / 2, target=lfrom[-1]) + gates.H(lfrom[-1])
        return Upair + Uladder + Ur + Uladder.dagger() + Upair2

    def __str(self):
        if self.indices is not None:
            return "FermionicExcitation({})".format(str(self.indices))
        return "FermionicExcitation"

    def __repr__(self):
        return self.__str__()

    def is_convertable_to_qubit_excitation(self):
        """
        spin-paired double excitations (both electrons occupy the same spatial orbital and are excited to another spatial orbital)
        in the jordan-wigner representation are identical to 4-qubit excitations which can be compiled more efficient
        this function hels to automatically detect those cases
        Returns
        -------

        """
        return False
        if not self.transformation.lower().strip("_") == "jordanwigner": return False
        if not len(self.indices) == 2: return False
        if not self.indices[0][0] // 2 == self.indices[1][0] // 2: return False
        if not self.indices[0][1] // 2 == self.indices[1][1] // 2: return False
        return True
    def map_qubits(self, qubit_map: dict):
        mapped = deepcopy(self)
        mapped._target = tuple([qubit_map[i] for i in self.target])
        if self.control is not None:
            mapped._control = tuple([qubit_map[i] for i in self.control])
        if hasattr(self, "generator") and self.generator:
            mapped.generator = self.generator.map_qubits(qubit_map=qubit_map)
        if hasattr(self, "generators"):
            mapped.generators = [i.map_qubits(qubit_map=qubit_map) for i in self.generators]
        mapped.finalize()
        if hasattr(self, "generator"):
            mapped.generator = self.generator.map_qubits(qubit_map=qubit_map)
        if hasattr(self, "indices"):
            mapped.indices = [(qubit_map[t[0]],qubit_map[t[1]]) for t in self.indices]
        return mapped


def prepare_product_state(state: BitString) -> QCircuit:
    """Small convenience function

    Parameters
    ----------
    state :
        product state encoded into a bitstring
    state: BitString :


    Returns
    -------
    type
        unitary circuit which prepares the product state

    """
    result = QCircuit()
    for i, v in enumerate(state.array):
        if v == 1:
            result += gates.X(target=i)
    return result


@dataclass
class ParametersQC:
    """Specialization of ParametersHamiltonian"""
    basis_set: str = None  # Quantum chemistry basis set
    geometry: str = None  # geometry of the underlying molecule (units: Angstrom!),
    # this can be a filename leading to an .xyz file or the geometry given as a string
    description: str = ""
    multiplicity: int = 1
    charge: int = 0
    name: str = None
    frozen_core: bool = True
    
    def get_number_of_core_electrons(self):
        result = 0
        for atom in self.get_atoms():
            n=self.get_atom_number(atom)
            if n>2:
                result += 2 
            if n>10:
                result += 10-2
            if n>18:
                result += 18-10-2
            if n>36:
                result += 36-18-10-2
            if n>54:
                result += 54-36-18-10-2
            if n>86:
                result += 86-54-36-18-10-2
        return result

    @property
    def total_n_electrons(self, *args, **kwargs):
        return self.get_nuc_charge() - self.charge

    def get_nuc_charge(self):
        return sum(self.get_atom_number(name=atom) for atom in self.get_atoms())

    def get_atom_number(self, name):
        atom_numbers = {"h": 1, "he": 2, "li": 3, "be": 4, "b": 5, "c": 6, "n": 7, "o": 8, "f": 9, "ne": 10, "na": 11,
                        "mg": 12, "al": 13, "si": 14, "ph": 15, "s": 16, "cl": 17, "ar": 18}
        if name.lower() in atom_numbers:
            return atom_numbers[name.lower()]
        try:
            import periodictable as pt
            atom = list(name.lower())
            atom[0] = atom[0].upper()
            atom = ''.join(atom)
            element = pt.elements.symbol(atom)
            return element.number
        except:
            raise TequilaException(
                "can not assign atomic number to element {}\npip install periodictable will fix it".format(atom))

    def get_atoms(self):
        return [x[0] for x in self.get_geometry()]

    def __post_init__(self, *args, **kwargs):

        if self.name is None and self.geometry is None:
            raise TequilaException(
                "no geometry or name given to molecule\nprovide geometry=filename.xyz or geometry=`h 0.0 0.0 0.0\\n...`\nor name=whatever with file whatever.xyz being present")
        # auto naming
        if self.name is None:
            if ".xyz" in self.geometry:
                self.name = self.geometry.split(".xyz")[0]
                if self.description is None:
                    coord, description = self.read_xyz_from_file()
                    self.description = description
            else:
                atoms = self.get_atoms()
                atom_names = sorted(list(set(atoms)), key=lambda x: self.get_atom_number(x), reverse=True)
                if self.name is None:
                    drop_ones = lambda x: "" if x == 1 else x
                    self.name = "".join(["{}{}".format(x, drop_ones(atoms.count(x))) for x in atom_names])
            self.name = self.name.lower()

        if self.geometry is None:
            self.geometry = self.name + ".xyz"

        if ".xyz" in self.geometry and not os.path.isfile(self.geometry):
            raise TequilaException("could not find file for molecular coordinates {}".format(self.geometry))

    @property
    def filename(self):
        """ """
        return "{}_{}".format(self.name, self.basis_set)

    @property
    def molecular_data_param(self) -> dict:
        """:return: Give back all parameters for the MolecularData format from openfermion as dictionary"""
        return {'basis': self.basis_set, 'geometry': self.get_geometry(), 'description': self.description,
                'charge': self.charge, 'multiplicity': self.multiplicity, 'filename': self.filename
                }

    @staticmethod
    def format_element_name(string):
        """OpenFermion uses case sensitive hash tables for chemical elements
        I.e. you need to name Lithium: 'Li' and 'li' or 'LI' will not work
        this convenience function does the naming
        :return: first letter converted to upper rest to lower

        Parameters
        ----------
        string :


        Returns
        -------

        """
        assert (len(string) > 0)
        assert (isinstance(string, str))
        fstring = string[0].upper() + string[1:].lower()
        return fstring

    @staticmethod
    def convert_to_list(geometry):
        """Convert a molecular structure given as a string into a list suitable for openfermion

        Parameters
        ----------
        geometry :
            a string specifying a mol. structure. E.g. geometry="h 0.0 0.0 0.0\n h 0.0 0.0 1.0"

        Returns
        -------
        type
            A list with the correct format for openfermion E.g return [ ['h',[0.0,0.0,0.0], [..]]

        """
        result = []
        # Remove blank lines
        lines = [l for l in geometry.split("\n") if l]

        for line in lines:
            words = line.split()

            # Pad coordinates
            if len(words) < 4:
                words += [0.0] * (4 - len(words))

            try:
                tmp = (ParametersQC.format_element_name(words[0]),
                       (float(words[1]), float(words[2]), float(words[3])))
                result.append(tmp)
            except ValueError:
                print("get_geometry list unknown line:\n ", line, "\n proceed with caution!")
        return result

    def get_geometry_string(self) -> str:
        """returns the geometry as a string
        :return: geometry string

        Parameters
        ----------

        Returns
        -------

        """
        if self.geometry.split('.')[-1] == 'xyz':
            geomstring, comment = self.read_xyz_from_file(self.geometry)
            if comment is not None:
                self.description = comment
            return geomstring
        else:
            return self.geometry

    def get_geometry(self):
        """Returns the geometry
        If a xyz filename was given the file is read out
        otherwise it is assumed that the geometry was given as string
        which is then reformatted as a list usable as input for openfermion
        :return: geometry as list
        e.g. [(h,(0.0,0.0,0.35)),(h,(0.0,0.0,-0.35))]
        Units: Angstrom!

        Parameters
        ----------

        Returns
        -------

        """
        if self.geometry.split('.')[-1] == 'xyz':
            geomstring, comment = self.read_xyz_from_file(self.geometry)
            if self.description == '':
                self.description = comment
            return self.convert_to_list(geomstring)
        elif self.geometry is not None:
            return self.convert_to_list(self.geometry)
        else:
            raise Exception("Parameters.qc.geometry is None")

    @staticmethod
    def read_xyz_from_file(filename):
        """Read XYZ filetype for molecular structures
        https://en.wikipedia.org/wiki/XYZ_file_format
        Units: Angstrom!

        Parameters
        ----------
        filename :
            return:

        Returns
        -------

        """
        with open(filename, 'r') as file:
            content = file.readlines()
            natoms = int(content[0])
            comment = str(content[1]).strip('\n')
            coord = ''
            for i in range(natoms):
                coord += content[2 + i]
            return coord, comment
    def get_xyz(self)->str:
        geom = self.parameters.get_geometry()
        f = ''
        f += f'{len(geom)}\n'
        f += f'{self.parameters.name}\n'
        for at in geom:
            f += f'{at[0]} {at[1][0]} {at[1][1]} {at[1][2]}\n'
        return f

@dataclass
class ClosedShellAmplitudes:
    """
    Helper Class for clasical amplitudes
    used internally
    """
    tIjAb: numpy.ndarray = None
    tIA: numpy.ndarray = None

    def make_parameter_dictionary(self, threshold=1.e-8, screening=True):
        """

        Parameters
        ----------
        threshold :
             (Default value = 1.e-8)

        Returns
        -------

        """
        variables = {}
        if self.tIjAb is not None:
            nvirt = self.tIjAb.shape[2]
            nocc = self.tIjAb.shape[0]
            assert (self.tIjAb.shape[1] == nocc and self.tIjAb.shape[3] == nvirt)
            for (I, J, A, B), value in numpy.ndenumerate(self.tIjAb):
                if not numpy.isclose(value, 0.0, atol=threshold) or not screening:
                    variables[(nocc + A, I, nocc + B, J)] = value
        if self.tIA is not None:
            nocc = self.tIA.shape[0]
            for (I, A), value, in numpy.ndenumerate(self.tIA):
                if not numpy.isclose(value, 0.0, atol=threshold) or not screening:
                    variables[(A + nocc, I)] = value
        return dict(sorted(variables.items(), key=lambda x: numpy.abs(x[1]), reverse=True))


@dataclass
class Amplitudes:
    """
    Helper class for classical Coupled-Cluster Amplitudes
    We adopt the Psi4 notation for consistency
    I,A for alpha
    i,a for beta

    Parameters
    ----------

    Returns
    -------

    """

    @classmethod
    def from_closed_shell(cls, cs: ClosedShellAmplitudes):
        """
        Initialize from closed-shell Amplitude structure

        Parameters
        ----------
        cs: ClosedShellAmplitudes :


        Returns
        -------

        """
        tijab = cs.tIjAb - numpy.einsum("ijab -> ijba", cs.tIjAb, optimize='greedy')
        return cls(tIjAb=cs.tIjAb, tIA=cs.tIA, tiJaB=cs.tIjAb, tia=cs.tIA, tijab=tijab, tIJAB=tijab)

    tIjAb: numpy.ndarray = None
    tIA: numpy.ndarray = None
    tiJaB: numpy.ndarray = None
    tijab: numpy.ndarray = None
    tIJAB: numpy.ndarray = None
    tia: numpy.ndarray = None

    def make_parameter_dictionary(self, threshold=1.e-8):
        """

        Parameters
        ----------
        threshold :
             (Default value = 1.e-8)
             Neglect amplitudes below the threshold

        Returns
        -------
        Dictionary of tequila variables (hash is in the style of (a,i,b,j))

        """
        variables = {}
        if self.tIjAb is not None:
            nvirt = self.tIjAb.shape[2]
            nocc = self.tIjAb.shape[0]
            assert (self.tIjAb.shape[1] == nocc and self.tIjAb.shape[3] == nvirt)

            for (I, j, A, b), value in numpy.ndenumerate(self.tIjAb):
                if not numpy.isclose(value, 0.0, atol=threshold):
                    variables[(2 * (nocc + A), 2 * I, 2 * (nocc + b) + 1, j + 1)] = value
            for (i, J, a, B), value in numpy.ndenumerate(self.tiJaB):
                if not numpy.isclose(value, 0.0, atol=threshold):
                    variables[(2 * (nocc + a) + 1, 2 * i + 1, 2 * (nocc + B), J)] = value
            for (i, j, a, b), value in numpy.ndenumerate(self.tijab):
                if not numpy.isclose(value, 0.0, atol=threshold):
                    variables[(2 * (nocc + a) + 1, 2 * i + 1, 2 * (nocc + b) + 1, j + 1)] = value
            for (I, J, A, B), value in numpy.ndenumerate(self.tijab):
                if not numpy.isclose(value, 0.0, atol=threshold):
                    variables[(2 * (nocc + A), 2 * I, 2 * (nocc + B), J)] = value

        if self.tIA is not None:
            nocc = self.tIjAb.shape[0]
            assert (self.tia.shape[0] == nocc)
            for (I, A), value, in numpy.ndenumerate(self.tIA):
                if not numpy.isclose(value, 0.0, atol=threshold):
                    variables[(2 * (A + nocc), 2 * I)] = value
            for (i, a), value, in numpy.ndenumerate(self.tIA):
                if not numpy.isclose(value, 0.0, atol=threshold):
                    variables[(2 * (a + nocc) + 1, 2 * i + 1)] = value

        return variables


class NBodyTensor:
    """ Convenience class for handling N-body tensors """

    class Ordering:
        """
        Convenience to keep track of aliases in odering names for two body integrals
        i.e. Mulliken/Chem/1122
             Dirac/Phys/1212
             openfermion/1221
        """

        def __init__(self, scheme):
            if hasattr(scheme, "_scheme"):
                scheme = scheme._scheme
            elif hasattr(scheme, "scheme"):
                scheme = scheme.scheme
            self._scheme = self.assign_scheme(scheme)

        def assign_scheme(self, scheme):
            if scheme is None:
                return "chem"
            else:
                scheme = str(scheme)

            if scheme.lower() in ["mulliken", "chem", "c", "1122"]:
                return "chem"
            elif scheme.lower() in ["dirac", "phys", "p", "1212"]:
                return "phys"
            elif scheme.lower() in ["openfermion", "of", "o", "1221"]:
                return "of"
            else:
                raise TequilaException(
                    "Unknown two-body tensor scheme {}. Supported are dirac, mulliken, and openfermion".format(scheme))

        def is_phys(self):
            return self._scheme == "phys"

        def is_chem(self):
            return self._scheme == "chem"

        def is_of(self):
            return self._scheme == "of"

        def __str__(self):
            return self._scheme

    def identify_ordering(self, trials=25):
        if len(self.shape) != 4:
            return None
        chem=False
        phys=False
        of=False
        if self._verify_ordering_mulliken(trials=trials):
            chem=self.Ordering(scheme="mulliken")
        if self._verify_ordering_dirac(trials=trials):
            phys=self.Ordering(scheme="dirac")
        if self._verify_ordering_of(trials=trials):
            of=self.Ordering(scheme="openfermion")

        uniqueness = (chem,phys,of)
        if not uniqueness.count(False) == 2 and trials<100:
            return self.identify_ordering(trials=trials*2)
        if chem: return self.Ordering(scheme="chem")
        elif phys: return self.Ordering(scheme="phys")
        elif of: return self.Ordering(scheme="openfermion")
        else:
            raise Exception("NBTensor ordering could not be identified")

    def _verify_ordering_dirac(self, trials=100):
        if len(self.shape) != 4:
            return False
        # dirac ordering: ijkl = <ij|kl> i.e 1212
        # check for two_body symetries: <ij|kl> = <kj|il> , <il|kj>
        elems = self.elems
        n = self.shape[0]
        for _ in range(trials):
            idx = numpy.random.randint(0,n,4)
            test1 = numpy.isclose(elems[idx[0],idx[1],idx[2],idx[3]],elems[idx[2],idx[1],idx[0],idx[3]], atol=1.e-4)
            test2 = numpy.isclose(elems[idx[0],idx[1],idx[2],idx[3]],elems[idx[0],idx[3],idx[2],idx[1]], atol=1.e-4)
            test3 = numpy.isclose(elems[idx[0],idx[1],idx[2],idx[3]],elems[idx[2],idx[3],idx[0],idx[1]], atol=1.e-4)
            if not (test1 and test2 and test3):
                return False

        return True

    def _verify_ordering_mulliken(self, trials=100):
        if len(self.shape) != 4:
            return False
        # mulliken ordering: ijkl = (ij|kl) i.e 1122
        elems = self.elems
        n = self.shape[0]
        for _ in range(trials):
            idx = numpy.random.randint(0,n,4)
            test1 = numpy.isclose(elems[idx[0],idx[1],idx[2],idx[3]],elems[idx[1],idx[0],idx[2],idx[3]], atol=1.e-4)
            test2 = numpy.isclose(elems[idx[0],idx[1],idx[2],idx[3]],elems[idx[0],idx[1],idx[3],idx[2]], atol=1.e-4)
            test3 = numpy.isclose(elems[idx[0],idx[1],idx[2],idx[3]],elems[idx[1],idx[0],idx[3],idx[2]], atol=1.e-4)
            if not (test1 and test2 and test3):
                return False

        return True

    def _verify_ordering_of(self, trials=100):
        if len(self.shape) != 4:
            return False
        # openfermion ordering: ijkl = [ij|kl] i.e 1221
        elems = self.elems
        n = self.shape[0]
        for _ in range(trials):
            idx = numpy.random.randint(0,n,4)
            test1 = numpy.isclose(elems[idx[0],idx[1],idx[2],idx[3]],elems[idx[3],idx[1],idx[2],idx[0]], atol=1.e-4)
            test2 = numpy.isclose(elems[idx[0],idx[1],idx[2],idx[3]],elems[idx[0],idx[2],idx[1],idx[3]], atol=1.e-4)
            test3 = numpy.isclose(elems[idx[0],idx[1],idx[2],idx[3]],elems[idx[3],idx[2],idx[1],idx[0]], atol=1.e-4)
            if not (test1 and test2 and test3):
                return False

        return True

    def __init__(self, elems: numpy.ndarray = None, active_indices: list = None, ordering: str = None,
                 size_full: int = None, verify=False):
        """
        Parameters
        ----------
        elems: Tensor data as numpy array
        active_indices: List of active indices in total ordering
        ordering: Ordering scheme for two body tensors
        "dirac" or "phys": <12|g|12>
            .. math::
                g_{pqrs} = \\int d1 d2 p(1)q(2) g(1,2) r(1)s(2)
        "mulliken" or "chem": (11|g|22)
            .. math::
                g_{pqrs} = \\int d1 d2 p(1)r(2) g(1,2) q(1)s(2)
        "openfermion":
            .. math:: [12|g|21]
                g_{gqprs} = \\int d1 d2 p(1)q(2) g(1,2) s(1)r(2)

        size_full
        """

        # Set elements
        self.elems = elems
        # Active indices only as list of indices (e.g. spatial orbital indices), not as a dictionary of irreducible
        # representations
        if active_indices is not None:
            self.active_indices = active_indices
        self._passive_indices = None
        self._full_indices = None
        self._indices_set: bool = False

        # Determine order of tensor
        # Assume, that tensor is entered in desired shape, not as flat array.
        self.order = len(self.elems.shape)
        # Can use size_full < self.elems.shape[0] -> 'full' space is to be considered a subspace as well
        if size_full is None:
            self._size_full = self.elems.shape[0]
        else:
            self._size_full = size_full
        # 2-body tensors (<=> order 4) currently allow reordering
        if self.order == 4:
            if ordering is None:
                ordering = self.identify_ordering()
            elif verify:
                try: # some RDMs are really sloppy (depends on backend)
                    auto_ordering=self.identify_ordering()
                    if auto_ordering is not ordering:
                        warnings.warn("Auto identified ordering of NBTensor does not match given ordering: {} vs {}".format(auto_ordering, ordering))
                except Exception as E:
                    warnings.warn("could not verify odering {}".format(ordering))
            self.ordering = self.Ordering(ordering)
        else:
            if ordering is not None:
                raise Exception("Ordering only implemented for tensors of order 4 / 2-body tensors.")
            self.ordering = None

    @property
    def shape(self, *args, **kwargs):
        return self.elems.shape

    def sub_lists(self, idx_lists: list = None) -> numpy.ndarray:
        """
        Get subspace of tensor by a set of index lists
        according to hPQ.sub_lists(idx_lists=[p, q]) = [hPQ for P in p and Q in q]

        This essentially is an implementation of a non-contiguous slicing using numpy.take

        Parameters
        ----------
            idx_lists :
                List of lists, each defining the desired subspace per axis
                Size needs to match order of tensor, and lists successively correspond to axis=0,1,2,...,N

        Returns
        -------
            out :
                Sliced tensor as numpy.ndarray
        """
        # Check if index list has correct size
        if len(idx_lists) != self.order:
            raise Exception("Need to pass an index list for each dimension!" +
                            " Length of idx_lists needs to match order of tensor.")

        # Perform slicing via numpy.take
        out = self.elems
        for ax in range(self.order):
            if idx_lists[ax] is not None:  # None means, we want the full space in this direction
                out = numpy.take(out, idx_lists[ax], axis=ax)

        return out

    def set_index_lists(self):
        """ Set passive and full index lists based on class inputs """
        tmp_size = self._size_full
        if self._size_full is None:
            tmp_size = self.elems.shape[0]

        self._passive_indices = [i for i in range(tmp_size)
                                 if i not in self.active_indices]
        self._full_indices = [i for i in range(tmp_size)]

    def sub_str(self, name: str) -> numpy.ndarray:
        """
        Get subspace of tensor by a string
        Currently is able to resolve an active space, named 'a', full space 'f', and the complement 'p' = 'f' - 'a'.
        Full space in this context may also be smaller than actual tensor dimension.

        The specification of active space in this context only allows to pick a set from a list of orbitals, and
        is not able to resolve an active space from irreducible representations.

        Example for one-body tensor:
        hPQ.sub_lists(name='ap') = [hPQ for P in active_indices and Q in _passive_indices]

        Parameters
        ----------
            name :
                String specifying the desired subspace, elements need to be a (active), f (full), p (full - active)

        Returns
        -------
            out :
                Sliced tensor as numpy.ndarray
        """
        if not self._indices_set:
            self.set_index_lists()
            self._indices_set = True

        if name is None:
            raise Exception("No name specified.")
        if len(name) != self.order:
            raise Exception("Name does not match order of the tensor.")
        if self.active_indices is None:
            raise Exception("Need to set an active space in order to call this function.")

        idx_lists = []
        # Parse name as string of space indices
        for char in name:
            if char.lower() == 'a':
                idx_lists.append(self.active_indices)
            elif char.lower() == 'p':
                idx_lists.append(self._passive_indices)
            elif char.lower() == 'f':
                if self._size_full is None:
                    idx_lists.append(None)
                else:
                    idx_lists.append(self._full_indices)
            else:
                raise Exception("Need to specify a valid letter (a,p,f).")

        out = self.sub_lists(idx_lists)

        return out

    def reorder(self, to: str = 'of'):
        """
        Function to reorder tensors according to some convention.

        Parameters
        ----------
        to :
            Ordering scheme of choice.
            'openfermion', 'of' (default) :
                openfermion - ordering, corresponds to integrals of the type
                h^pq_rs = int p(1)* q(2)* O(1,2) r(2) s(1) (O(1,2)
                with operators a^pq_rs = a^p a^q a_r a_s (a^p == a^dagger_p)
                currently needed for dependencies on openfermion-library
            'chem', 'c' :
                quantum chemistry ordering, collect particle terms,
                more convenient for real-space methods
                h^pq_rs = int p(1) q(1) O(1,2) r(2) s(2)
                This is output by psi4
            'phys', 'p' :
                typical physics ordering, integrals of type
                h^pq_rs = int p(1)* q(2)* O(1,2) r(1) s(2)
                with operators a^pq_rs = a^p a^q a_s a_r

            Returns
            -------
        """
        if self.order != 4:
            warnings.warn('Reordering currently only implemented for two-body tensors.')
            return self

        to = self.Ordering(scheme=to)

        if self.ordering == to:
            return self
        elif self.ordering.is_chem():
            if to.is_of():
                self.elems = numpy.einsum("psqr -> pqrs", self.elems, optimize='greedy')
            elif to.is_phys():
                self.elems = numpy.einsum("prqs -> pqrs", self.elems, optimize='greedy')
        elif self.ordering.is_of():
            if to.is_chem():
                self.elems = numpy.einsum("pqrs -> psqr", self.elems, optimize='greedy')
            elif to.is_phys():
                self.elems = numpy.einsum("pqrs -> pqsr", self.elems, optimize='greedy')
        elif self.ordering.is_phys():
            if to.is_chem():
                self.elems = numpy.einsum("pqrs -> prqs", self.elems, optimize='greedy')
            elif to.is_of():
                self.elems = numpy.einsum("pqsr -> pqrs", self.elems, optimize='greedy')

        self.ordering=to
        return self


@dataclass
class OrbitalData:
    irrep: str = None  # irrep of symmetry group (if assigned)
    idx_irrep: int = None  # index within the irrep
    idx_total: int = None  # index within the total set of orbitals
    idx: int = None  # index within the active space
    energy: float = None  # energy assigned to orbital
    occ: float = None  # occupation number assigned to orbital
    pair: tuple = None  # potential electron pair that the orbital is assigned to

    def __post_init__(self):
        # backward compatibility
        if self.pair is not None and len(self.pair) == 1:
            self.pair = (self.pair[0], self.pair[0])
            self.occ = 2.0  # mark as reference

    def __str__(self):
        return "{"+"{}".format("".join(["{}:{}, ".format(k,v) for k,v in self.__dict__.items() if v is not None])).rstrip().rstrip(",")+"}"

class IntegralManager:
    """
    Manage Basis Integrals of Quantum Chemistry
    All integrals are held in their original basis, the corresponding mo-coefficients have to be passed down
    and are usually held by the QuantumChemistryBaseClass
    """
    _overlap_integrals: numpy.ndarray = None
    _one_body_integrals: numpy.ndarray = None
    _two_body_integrals: NBodyTensor = None
    _constant_term: float = None
    _basis_name: str = "unknown"
    _orbital_type: str = "unknown" # e.g. "HF", "PNO", "native"
    _orbital_coefficients: numpy.ndarray = None
    _active_space: ActiveSpaceData = None
    _orbitals: typing.List[OrbitalData] = None

    def __init__(self, one_body_integrals, two_body_integrals,
                 basis_name="unknown", orbital_type="unknown",
                 constant_term=0.0, orbital_coefficients=None, active_space=None, overlap_integrals=None, orbitals=None, *args, **kwargs):
        self._one_body_integrals = one_body_integrals
        self._two_body_integrals = two_body_integrals
        self._constant_term = constant_term
        self._basis_name = basis_name
        self._orbital_type = orbital_type

        assert len(self._one_body_integrals.shape) == 2
        assert len(self._two_body_integrals.shape) == 4
        try:
            two_body_integrals = two_body_integrals.reorder(to="chem")
        except Exception as E:
            raise TequilaException(
                "{}\ntwo_body_integrals given in wrong format. Needs to be a tq.chemistry.NBodyTensor in chem ordering.\n{} with ordering={}".format(
                    str(E), str(type(two_body_integrals)), str(two_body_integrals.ordering)))

        for i in range(4):
            assert self._one_body_integrals.shape[0] == self._two_body_integrals.elems.shape[i]
        assert self._one_body_integrals.shape[0] == self._one_body_integrals.shape[1]

        if overlap_integrals is None:
            overlap_integrals = numpy.eye(one_body_integrals.shape[0])
        self._overlap_integrals = overlap_integrals
        assert self._overlap_integrals.shape == self._one_body_integrals.shape

        if orbital_coefficients is None:
            # default are symmetrically orthogonalized orbitals in the given basis
            orbital_coefficients = self.get_orthonormalized_orbital_coefficients()
        self._orbital_coefficients = orbital_coefficients

        if orbitals is None:
            orbitals = [OrbitalData(idx_total=i, idx=i) for i in range(one_body_integrals.shape[0])]

        self._orbitals = orbitals
        self.active_space = active_space
    
    def get_orthonormalized_orbital_coefficients(self):
        """
        Computes orbitals in this basis that are orthonormal (through loewdin orthonormalization)

        Returns
        -------
        coefficient matrix of orthonormalized orbitals
        """
        if self.basis_is_orthogonal():
            return numpy.eye(self._one_body_integrals.shape[0])

        S = self._overlap_integrals
        sv, U = numpy.linalg.eigh(S)
        s = numpy.diag(numpy.asarray([1.0 / numpy.sqrt(x) for x in sv]))
        C = U.dot(s.dot(U.transpose()))
        return C

    @property
    def active_orbitals(self):
        return [self._orbitals[i] for i in self._active_space.active_orbitals]

    @property
    def orbitals(self):
        return self._orbitals

    @property
    def active_space(self):
        return self._active_space

    @active_space.setter
    def active_space(self, other):
        self._active_space = other
        for x in self._orbitals:
            x.idx = None
        for ii,i in enumerate(other.active_orbitals):
            self._orbitals[i].idx = ii

    @property
    def reference_orbitals(self):
        return [self._orbitals[i] for i in self.active_space.reference_orbitals]

    @property
    def active_reference_orbitals(self):
        return [self._orbitals[i] for i in self.active_space.active_orbitals if i in self.active_space.reference_orbitals]

    @property
    def overlap_integrals(self):
        """
        Returns
        -------
        Overlap integrals in given basis (using basis functions, not molecular orbitals. No active space considered)
        """
        return self._overlap_integrals

    @property
    def one_body_integrals(self):
        """
        Returns
        -------
        one_body integrals in given basis (using basis functions, not molecular orbitals. No active space considered)
        """
        return self._one_body_integrals

    @property
    def two_body_integrals(self):
        """
        Returns
        -------
        two-body orbitals in given basis (using basis functions, not molecular orbitals. No active space considered)
        ordering is "chem" i.e. Mulliken i.e. integrals_{abcd} = <ac|g|bd>
        """
        return self._two_body_integrals

    @property
    def constant_term(self):
        """
        Returns
        -------
        return constant term (usually nuclear repulsion). No active space considered
        """
        return self._constant_term

    @property
    def orbital_coefficients(self):
        """
        second index is the orbital index, first the basis index
        Returns
        -------
        orbital coefficient matrix C_{basis,orbital}
        """
        return self._orbital_coefficients

    @orbital_coefficients.setter
    def orbital_coefficients(self, other):
        self.verify_orbital_coefficients(orbital_coefficients=other)
        self._orbital_coefficients = other
        for i,x in enumerate(self._orbitals):
            y = OrbitalData(idx=x.idx, idx_total=x.idx_total)
            self._orbitals[i] = y
    
    def transform_to_native_orbitals(self):
        """
        Transform orbitals to orthonormal functions closest to the native basis
        """
        c = self.get_orthonormalized_orbital_coefficients()
        self.orbital_coefficients=c
        self._orbital_type="orthonormalized-{}-basis".format(self._basis_name)

    def is_unitary(self, U):
        if len(U.shape) != 2: return False
        if U.shape[0] != U.shape[1]: return False
        test = (U.conj().T).dot(U) - numpy.eye(U.shape[0])
        if not numpy.isclose(numpy.linalg.norm(test), 0.0): return False
        return True

    def transform_orbitals(self, U, name=None):
        """
        Transform orbitals
        Parameters
        ----------
        U: second index is new orbital indes, first is old orbital index (summed over)

        Returns
        -------
        updates the structure with new orbitals: c = cU
        """
        assert self.is_unitary(U)
        self.orbital_coefficients = numpy.einsum("ix, xj -> ij", self.orbital_coefficients, U, optimize="greedy")
        if name is None:
            self._orbital_type += "-transformed"
        else:
            self._orbital_type = name

    def get_integrals(self, orbital_coefficients=None, ordering="openfermion", ignore_active_space=False, *args, **kwargs):
        """
        Get all molecular integrals in given orbital basis (determined by orbital_coefficients in self or the ones passed here)
        active space is considered if not explicitly ignored
        Parameters
        ----------
        orbital_coefficients: orbital coefficients in the given basis (first index is basis, second index is orbitals). Need to go over full basis (no active space)
        ordering: ordering of the two-body integrals (default is openfermion)
        ignore_active_space: ignore active space and give back full integrals

        Returns
        -------

        """
        if orbital_coefficients is None:
            orbital_coefficients = self.orbital_coefficients

        c = self.constant_term
        h = self._get_transformed_one_body_integrals(orbital_coefficients=orbital_coefficients)
        g = self._get_transformed_two_body_integrals(orbital_coefficients=orbital_coefficients, ordering=ordering)
        if not ignore_active_space and self._active_space is not None:

            g = g.reorder(to="openfermion").elems

            active_integrals = get_active_space_integrals(one_body_integrals=h, two_body_integrals=g,
                                                          occupied_indices=self._active_space.frozen_reference_orbitals,
                                                          active_indices=self._active_space.active_orbitals)

            c = active_integrals[0] + c

            h = active_integrals[1]
            g = NBodyTensor(elems=active_integrals[2], ordering="openfermion")
        g.reorder(to=ordering)
        return c, h, g

    def _get_transformed_one_body_integrals(self, orbital_coefficients=None, verify=True):
        if orbital_coefficients is None:
            orbital_coefficients = self.orbital_coefficients
        elif verify:
            assert self.verify_orbital_coefficients(orbital_coefficients=orbital_coefficients)
        h = self.one_body_integrals
        h = numpy.einsum("ix, xj -> ij", h, orbital_coefficients, optimize='greedy')
        h = numpy.einsum("xj, xi -> ij", h, orbital_coefficients, optimize='greedy')

        return h

    def _get_transformed_two_body_integrals(self, orbital_coefficients=None, ordering="openfermion", verify=True):
        if orbital_coefficients is None:
            orbital_coefficients = self.orbital_coefficients
        elif verify:
            assert self.verify_orbital_coefficients(orbital_coefficients=orbital_coefficients)

        g = self.two_body_integrals
        g = g.reorder("chem").elems
        g = numpy.einsum("ijkx, xl -> ijkl", g, orbital_coefficients, optimize='greedy')
        g = numpy.einsum("ijxl, xk -> ijkl", g, orbital_coefficients, optimize='greedy')
        g = numpy.einsum("ixkl, xj -> ijkl", g, orbital_coefficients, optimize='greedy')
        g = numpy.einsum("xjkl, xi -> ijkl", g, orbital_coefficients, optimize='greedy')
        g = NBodyTensor(elems=numpy.asarray(g), ordering='chem')
        g = g.reorder(to=ordering)

        return g

    def verify_orbital_coefficients(self, orbital_coefficients, tolerance=1.e-5):
        """
        Verify if orbital coefficients are valid (i.e. if they define a orthonormal set of orbitals)
        Parameters
        ----------
        orbital_coefficients: the orbital coefficients C_ij with i:basis and j:orbitals
        tolerance

        Returns
        -------
        True or False depending if the overlap matrix of the basis is transformed to a unit matrix

        """
        S = self.overlap_integrals
        St = numpy.einsum("ix, xj -> ij", S, orbital_coefficients, optimize='greedy')
        St = numpy.einsum("xj, xi -> ij", St, orbital_coefficients, optimize='greedy')
        return numpy.linalg.norm(St - numpy.eye(S.shape[0])) < tolerance

    def basis_is_orthogonal(self, tolerance=1.e-5):
        S = self.overlap_integrals
        return numpy.linalg.norm(S - numpy.eye(S.shape[0])) < tolerance

    def active_space_is_trivial(self):
        return len(self.active_orbitals) == len(self.orbitals)

    def __str__(self):
        result = "\nIntegralManager:\n"
        result+= "ActiveSpace:\n"
        result+= str(self.active_space)
        result+= "Orbitals:\n"
        for x in self.orbitals:
            result += str(x) + "\n"
        return result

    def print_basis_info(self, print_coefficients=True, *args, **kwargs) -> None:
        print("{:15} : {}".format("basis_name", self._basis_name), *args, **kwargs)
        print("{:15} : {}".format("orbital_type", self._orbital_type), *args, **kwargs)
        print("{:15} : {}".format("orthogonal basis", self.basis_is_orthogonal()), *args, **kwargs)
        print("{:15} : {}".format("basis functions", self.one_body_integrals.shape[0]), *args, **kwargs)
        print("{:15} : {}".format("active orbitals", [o.idx_total for o in self.active_orbitals]), *args, **kwargs)
        print("{:15} : {}".format("reference", [x.idx_total for x in self.reference_orbitals]), *args, **kwargs)

        if not print_coefficients: return

        print("Current Orbitals", *args, **kwargs)
        for i,x in enumerate(self.orbitals):
            print(x, *args, **kwargs)
            print("coefficients: ", self.orbital_coefficients[:,i], *args, **kwargs)
