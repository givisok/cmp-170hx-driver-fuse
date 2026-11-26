import torch, time
for i in range(3):
    torch.cuda.set_device(i)
    torch.cuda.synchronize()
    a = torch.randn(8192, 8192, device=i, dtype=torch.float16)
    b = torch.randn(8192, 8192, device=i, dtype=torch.float16)
    for _ in range(3): c = a @ b
    torch.cuda.synchronize(); t0 = time.perf_counter()
    for _ in range(30): c = a @ b
    torch.cuda.synchronize(); dt = (time.perf_counter() - t0) / 30
    x = torch.zeros(64, device=i)
    for _ in range(10): y = x + 1
    torch.cuda.synchronize(); t0 = time.perf_counter()
    for _ in range(500): y = x + 1
    torch.cuda.synchronize(); lt = (time.perf_counter() - t0) / 500 * 1e6
    p = torch.cuda.get_device_properties(i)
    print(f"GPU{i} [{p.name}] SMs={p.multi_processor_count} mem={p.total_memory//2**30}GiB matmul={dt*1e3:.2f}ms ({2*8192**3/dt/1e12:.1f} TFLOPS) smallkernel={lt:.1f}us", flush=True)
    del a, b, c, x, y
