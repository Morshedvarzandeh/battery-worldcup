"""Registry of public datasets: where they live, how they are licensed, how to cite them.

Raw data are never committed to this repository. Entries with ``files`` can be downloaded
with :func:`battery_worldcup.data.cache.download`; entries without registered files must be
fetched manually from ``url`` (a licence click-through or a changing download link) and then
converted with the matching loader. ``licence_verified`` is False until a maintainer has read
the licence at the source.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RemoteFile:
    url: str
    filename: str
    sha256: str | None = None
    size_bytes: int | None = None


@dataclass(frozen=True)
class DatasetEntry:
    key: str
    name: str
    institution: str
    url: str
    citation: str
    chemistry: str
    n_cells: str
    wave: int
    loader: str | None = None
    doi: str | None = None
    licence: str = "see source"
    licence_verified: bool = False
    files: tuple[RemoteFile, ...] = field(default_factory=tuple)
    notes: str = ""


_ENTRIES: tuple[DatasetEntry, ...] = (
    DatasetEntry(
        key="synthetic",
        name="Synthetic cells (built in)",
        institution="battery-worldcup",
        url="battery_worldcup.data.synthetic",
        citation="none",
        chemistry="synthetic NMC-like",
        n_cells="configurable",
        wave=0,
        loader="synthetic",
        licence="MIT",
        licence_verified=True,
        notes="Generated on the fly; used by the tests and the tutorials.",
    ),
    DatasetEntry(
        key="nasa",
        name="NASA PCoE Battery Data Set",
        institution="NASA Ames Prognostics Center of Excellence",
        url="https://www.nasa.gov/intelligent-systems-division/discovery-and-systems-health/pcoe/pcoe-data-set-repository/",
        citation="Saha, B. and Goebel, K. (2007). Battery Data Set. NASA Ames Prognostics Data Repository.",
        chemistry="18650, about 2 Ah",
        n_cells="about 34",
        wave=1,
        notes="MATLAB files B00xx.mat; one file per cell.",
    ),
    DatasetEntry(
        key="nasa_rw",
        name="NASA Randomized Battery Usage Data Set",
        institution="NASA Ames Prognostics Center of Excellence",
        url="https://www.nasa.gov/intelligent-systems-division/discovery-and-systems-health/pcoe/pcoe-data-set-repository/",
        citation="Bole, B., Kulkarni, C. and Daigle, M. (2014). Randomized Battery Usage Data Set. NASA Ames Prognostics Data Repository.",
        chemistry="18650",
        n_cells="28",
        wave=2,
    ),
    DatasetEntry(
        key="calce",
        name="CALCE CS2 and CX2",
        institution="Center for Advanced Life Cycle Engineering, University of Maryland",
        url="https://calce.umd.edu/battery-data",
        citation="CALCE Battery Research Group, University of Maryland. Battery data.",
        chemistry="LCO prismatic, 1.1 Ah (CS2) and 1.35 Ah (CX2)",
        n_cells="13 commonly used",
        wave=1,
        notes="Excel exports from Arbin cyclers; several files per cell.",
    ),
    DatasetEntry(
        key="oxford",
        name="Oxford Battery Degradation Dataset 1",
        institution="University of Oxford",
        url="https://ora.ox.ac.uk/objects/uuid:03ba4b01-cfed-46d3-9b1a-7d4a7bdf6fac",
        citation="Birkl, C. (2017). Oxford Battery Degradation Dataset 1. University of Oxford. See also Birkl et al. (2017), J. Power Sources.",
        chemistry="Kokam SLPB533459H4 pouch, 740 mAh",
        n_cells="8",
        wave=1,
        loader="oxford",
        notes="Single MATLAB file Oxford_Battery_Degradation_Dataset_1.mat.",
    ),
    DatasetEntry(
        key="matr",
        name="MATR (Severson 2019 and Attia 2020)",
        institution="MIT, Stanford and Toyota Research Institute",
        url="https://data.matr.io/1/",
        citation="Severson et al. (2019), Nature Energy; Attia et al. (2020), Nature.",
        chemistry="A123 APR18650M1A LFP/graphite, 1.1 Ah",
        n_cells="124 (Severson); about 180 with Attia",
        wave=1,
        notes="MATLAB batch files; BatteryML also distributes a processed form.",
    ),
    DatasetEntry(
        key="hust",
        name="HUST",
        institution="Huazhong University of Science and Technology",
        url="https://data.mendeley.com/datasets/nsc7hnsg4s/2",
        doi="10.17632/nsc7hnsg4s.2",
        citation="Ma et al. (2022). Real-time personalized health status prediction of lithium-ion batteries using deep transfer learning. Energy and Environmental Science.",
        chemistry="A123 LFP/graphite, 1.1 Ah",
        n_cells="77",
        wave=2,
    ),
    DatasetEntry(
        key="xjtu",
        name="XJTU battery dataset",
        institution="Xi'an Jiaotong University",
        url="https://wang-fujin.github.io/",
        citation="Wang et al. (2024). Physics-informed neural network for lithium-ion battery degradation stable modeling and prognosis. Nature Communications.",
        chemistry="LISHEN 18650 NCM (LiNi0.5Co0.2Mn0.3O2), 2 Ah",
        n_cells="55 in 6 batches",
        wave=2,
    ),
    DatasetEntry(
        key="snl",
        name="Sandia National Laboratories cycling study",
        institution="Sandia National Laboratories",
        url="https://www.batteryarchive.org/snl_study.html",
        citation="Preger et al. (2020). Degradation of commercial lithium-ion cells as a function of chemistry and cycling conditions. J. Electrochem. Soc.",
        chemistry="LFP (A123 1.1 Ah), NCA (Panasonic NCR18650B 3.2 Ah), NMC (LG Chem 18650HG2 3 Ah)",
        n_cells="about 61 on Battery Archive",
        wave=2,
        notes="CSV files in the Battery Archive format.",
    ),
    DatasetEntry(
        key="hnei",
        name="HNEI",
        institution="Hawaii Natural Energy Institute",
        url="https://www.batteryarchive.org/",
        citation="Devie, A., Baure, G. and Dubarry, M. (2018). Intrinsic variability in the degradation of a batch of commercial 18650 lithium-ion cells. Energies.",
        chemistry="NMC-LCO 18650, 2.8 Ah",
        n_cells="51 in the study; 14 on Battery Archive",
        wave=2,
        notes="CSV files in the Battery Archive format.",
    ),
    DatasetEntry(
        key="ulpur",
        name="UL-PUR",
        institution="Underwriters Laboratories and Purdue University",
        url="https://www.batteryarchive.org/",
        citation="Juarez-Robles et al. (2020). J. Electrochem. Soc. (see Battery Archive study page).",
        chemistry="commercial pouch cells",
        n_cells="10",
        wave=2,
        notes="CSV files in the Battery Archive format.",
    ),
    DatasetEntry(
        key="mich",
        name="Michigan (MICH, MICH_EXP)",
        institution="University of Michigan",
        url="https://www.batteryarchive.org/",
        citation="Mohtat et al. (2021). Reversible and irreversible expansion of lithium-ion batteries under a wide range of stress factors. J. Electrochem. Soc.",
        chemistry="NMC/graphite pouch",
        n_cells="(confirm)",
        wave=3,
        notes="CSV files in the Battery Archive format; includes expansion measurements.",
    ),
    DatasetEntry(
        key="rwth",
        name="RWTH Aachen ISEA cyclic aging data",
        institution="RWTH Aachen University",
        url="https://publications.rwth-aachen.de/record/818642",
        citation="Sauer, D. U. et al. (2021). Time-series cyclic aging data on 48 commercial NMC/graphite Sanyo/Panasonic UR18650E cylindrical cells. RWTH Aachen University.",
        chemistry="Sanyo/Panasonic UR18650E NMC/graphite",
        n_cells="48",
        wave=2,
    ),
    DatasetEntry(
        key="isu_ilcc",
        name="ISU-ILCC Battery Aging Dataset",
        institution="Iowa State University and Iowa Lakes Community College",
        url="https://doi.org/10.25380/IASTATE.22582234.V2",
        doi="10.25380/IASTATE.22582234.V2",
        citation="Hu, C. et al. ISU-ILCC Battery Aging Dataset (release 2.0, 2023). Iowa State University.",
        chemistry="NMC/graphite lithium-polymer pouch",
        n_cells="251 (238 in release 2.0)",
        wave=2,
    ),
    DatasetEntry(
        key="tongji_relax",
        name="Tongji voltage-relaxation dataset",
        institution="Tongji University and partners",
        url="https://doi.org/10.5281/zenodo.6405084",
        doi="10.5281/zenodo.6405084",
        citation="Zhu et al. (2022). Data-driven capacity estimation of commercial lithium-ion batteries from voltage relaxation. Nature Communications.",
        chemistry="18650 NCA, NCM and NCM+NCA",
        n_cells="130 across 3 sub-datasets",
        wave=2,
    ),
    DatasetEntry(
        key="cam_eis",
        name="Cambridge EIS dataset",
        institution="University of Cambridge",
        url="https://zenodo.org/",
        citation="Zhang et al. (2020). Identifying degradation patterns of lithium ion batteries from impedance spectroscopy using machine learning. Nature Communications.",
        chemistry="Eunicell LR2032 LCO coin cells, 45 mAh",
        n_cells="12",
        wave=2,
        notes="Zenodo record linked from the paper's data availability statement.",
    ),
    DatasetEntry(
        key="stanford_ev",
        name="Stanford EV driving-profile aging dataset",
        institution="Stanford Energy Control Lab",
        url="https://doi.org/10.1016/j.dib.2022.107995",
        doi="10.1016/j.dib.2022.107995",
        citation="Pozzato, G., Allam, A. and Onori, S. (2022). Lithium-ion battery aging dataset based on electric vehicle real-driving profiles. Data in Brief.",
        chemistry="LG INR21700-M50T NMC with graphite-silicon anode",
        n_cells="10",
        wave=2,
        notes="Hosted on the Open Science Framework; link in the paper.",
    ),
    DatasetEntry(
        key="stanford_2nd",
        name="Stanford second-life grid-storage aging dataset",
        institution="Stanford Energy Control Lab",
        url="https://pmc.ncbi.nlm.nih.gov/articles/PMC11639452/",
        citation="Second-life lithium-ion battery aging dataset based on grid storage cycling (2024). Data in Brief.",
        chemistry="retired NMC cells",
        n_cells="(confirm)",
        wave=3,
    ),
    DatasetEntry(
        key="dubarry_synth",
        name="Synthetic degradation-mode ICA and DVA sets",
        institution="Hawaii Natural Energy Institute",
        url="https://doi.org/10.1016/j.jpowsour.2020.228806",
        doi="10.1016/j.jpowsour.2020.228806",
        citation="Dubarry, M. and Beck, D. (2020). Big data training data for artificial intelligence-based Li-ion diagnosis and prognosis. J. Power Sources.",
        chemistry="synthetic LFP, NMC, NCA",
        n_cells="not applicable",
        wave=2,
    ),
)

REGISTRY: dict[str, DatasetEntry] = {e.key: e for e in _ENTRIES}


def get(key: str) -> DatasetEntry:
    try:
        return REGISTRY[key]
    except KeyError as exc:
        raise KeyError(f"unknown dataset {key!r}; known: {sorted(REGISTRY)}") from exc


def list_entries(wave: int | None = None) -> list[DatasetEntry]:
    entries = list(_ENTRIES)
    if wave is not None:
        entries = [e for e in entries if e.wave == wave]
    return entries
