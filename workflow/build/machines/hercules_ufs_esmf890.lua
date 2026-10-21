help([[
loads UFS Model prerequisites for Hercules/Intel with ESMF 8.9.0b14
(replaces ESMF 8.8.0: dual-mesh triangulation bug, ESMCI_MeshDual.C line 1143,
element-centered meshes crossing the antimeridian)
]])


prepend_path("MODULEPATH",
    "/apps/contrib/spack-stack/spack-stack-1.9.2/envs/esmf-8.9.0b14-oneapi-2024.2.1/install/modulefiles/Core")
prepend_path("MODULEPATH",
    "/apps/contrib/spack-stack/spack-stack-1.9.2/envs/esmf-8.9.0b14-oneapi-2024.2.1/install/modulefiles/intel-oneapi-mpi/2021.13-sqiixt7/gcc/13.3.0")
prepend_path("MODULEPATH",
    "/apps/contrib/spack-stack/spack-stack-1.9.2/envs/esmf-8.9.0b14-oneapi-2024.2.1/install/modulefiles/intel-oneapi-mpi/2021.13-sqiixt7/oneapi/2024.2.1")


stack_intel_ver = os.getenv("stack_intel_ver") or "2024.2.1"
load(pathJoin("stack-oneapi", stack_intel_ver))


stack_impi_ver = os.getenv("stack_impi_ver") or "2021.13"
load(pathJoin("stack-intel-oneapi-mpi", stack_impi_ver))


load("ufs-weather-model-env/1.0.0")


local old_env = "/apps/contrib/spack-stack/spack-stack-1.9.2/envs/ue-oneapi-2024.1.0/install/oneapi/2024.2.1"
prepend_path("CMAKE_PREFIX_PATH", old_env .. "/sp-2.5.0-odkysey")


setenv("CC",  "mpiicc")
setenv("CXX", "mpiicpc")
setenv("FC",  "mpiifort")
setenv("CMAKE_Platform", "hercules.intel")


whatis("Description: UFS build environment with ESMF 8.9.0b14 (antimeridian-safe)")
