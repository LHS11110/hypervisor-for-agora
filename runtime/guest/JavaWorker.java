import java.io.*;
import java.lang.reflect.Field;
import java.nio.charset.StandardCharsets;
import java.util.*;
import jdk.jshell.*;

/** One in-process JShell per language environment; the VM/cgroup is the boundary. */
public class JavaWorker {
    private static int evaluate(JShell shell, String source) {
        int status=0;
        for (SnippetEvent event:shell.eval(source)) {
            if(event.status()==Snippet.Status.REJECTED) {
                shell.diagnostics(event.snippet()).forEach(d->System.err.println(d.getMessage(Locale.ROOT)));
                status=1;
            }
            if(event.exception()!=null) { event.exception().printStackTrace(System.err);status=1; }
        }
        return status;
    }
    public static void main(String[] args) throws Exception {
        FileDescriptor fd=new FileDescriptor();
        Field field=FileDescriptor.class.getDeclaredField("fd");field.setAccessible(true);field.setInt(fd,Integer.parseInt(args[0]));
        BufferedReader input=new BufferedReader(new InputStreamReader(new FileInputStream(fd),StandardCharsets.US_ASCII));
        OutputStream control=new FileOutputStream(fd);
        List<String> options=new ArrayList<>(List.of("--release",args[1]));
        if(Boolean.parseBoolean(args[2]))options.add("--enable-preview");
        try(JShell shell=JShell.builder().executionEngine("local").compilerOptions(options.toArray(String[]::new)).out(System.out).err(System.err).build()) {
            if(args.length>3) {
                String classpath=java.nio.file.Files.readString(java.nio.file.Path.of(args[3])).trim();
                if(!classpath.isEmpty())shell.addToClasspath(classpath);
            }
            String line;
            while((line=input.readLine())!=null) {
                int status=0;
                try {
                    String[] fields=line.split(" ",2);
                    String inputFd=System.getenv("AGORA_STDIN_FD");
                    String inputPath=fields.length>1&&fields[1].equals("1")&&inputFd!=null?"/proc/self/fd/"+inputFd:"/dev/null";
                    System.setIn(new FileInputStream(inputPath));
                    String source=new String(Base64.getDecoder().decode(fields[0]),StandardCharsets.UTF_8);
                    while(!source.isBlank()&&status==0) {
                        var completion=shell.sourceCodeAnalysis().analyzeCompletion(source);
                        String snippet=completion.source();
                        if(snippet==null||snippet.isBlank()) {System.err.println("Incomplete Java code");status=1;break;}
                        List<SnippetEvent> events=shell.eval(snippet);
                        for(var event:events) {
                            if(event.status()==Snippet.Status.REJECTED) {
                                shell.diagnostics(event.snippet()).forEach(d->System.err.println(d.getMessage(Locale.ROOT)));status=1;
                            }
                            if(event.exception()!=null) {event.exception().printStackTrace(System.err);status=1;}
                            if(status==0&&event.status()==Snippet.Status.VALID&&event.causeSnippet()==null&&event.snippet() instanceof TypeDeclSnippet type) {
                                // A conventional class with main runs automatically. Ordinary types only register.
                                String name=type.name();
                                String invoke="try { java.lang.reflect.Method m="+name+".class.getDeclaredMethod(\"main\",String[].class); if(java.lang.reflect.Modifier.isStatic(m.getModifiers())){m.setAccessible(true);m.invoke(null,(Object)new String[0]);} } catch(java.lang.NoSuchMethodException ignored) {} catch(java.lang.reflect.InvocationTargetException e){throw new RuntimeException(e.getCause());} catch(java.lang.IllegalAccessException e){throw new RuntimeException(e);}";
                                status=evaluate(shell,invoke);
                            }
                        }
                        source=completion.remaining();
                    }
                } catch(Throwable error) {error.printStackTrace(System.err);status=1;}
                System.in.close();
                System.out.flush();System.err.flush();
                control.write(("{\"exitCode\":"+status+"}\n").getBytes(StandardCharsets.US_ASCII));control.flush();
            }
        }
    }
}
